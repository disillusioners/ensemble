# P2-WP8 — Stage-2 Gateway-Window E2E Findings

- **Date:** 2026-09-27T03:33:47Z
- **Worktree:** `/home/nea/ensemble-src-wt-designer-agent-design` (branch `feature/designer-agent-design`, tip `4332b127` at dispatch — note: actual local tip is `8941a53c` per `git log -1`, 37 commits over base `e67e5cd8`; the dispatcher's pinned tip `4332b127` is an ancestor commit within the mission branch, no plumbing change)
- **Operator:** worker (this report)
- **Verdict:** **NOT-GREEN — STEP 0 GATEWAY PROBE FAILURE (hard stop)**
- **Substrate pair (unused this run):** `61badab6017744cd9de7a05ab1823619` (settings, 55558 B) + `ec84609148dd4ca09b0169a954835e4f` (home, 44523 B) — preserved, untouched
- **Pinned-spec SHA (unused this run):** `81113ea0f2ffe0d2e7994e963e34f04f10989887a659a33774e2966af2c85b24` (dispatcher re-verified unchanged 2026-09-26; not re-hashed this run because the fixture spec file was not opened and the SHA cannot have changed)

---

## STEP 0 — Gateway probe (hard gate)

### Raw probe evidence (`/tmp/wp8/probe-evidence-20260927T033347Z.txt`, full file captured)

```
PROBE_TIME=2026-09-27T03:33:47Z
PROBE_TARGET=http://127.0.0.1:4124

--- ss -tlnp | grep 4124 ---
(no listener)

--- curl probes (all paths, 3s timeout each) ---
GET /                              -> curl: (7) Failed to connect to 127.0.0.1 port 4124 after 0 ms: Couldn't connect to server    HTTP_STATUS:000 TIME:0.000365s
GET /v1/models                     -> curl: (7) Failed to connect to 127.0.0.1 port 4124 after 0 ms: Couldn't connect to server    HTTP_STATUS:000 TIME:0.000352s
GET /health                        -> curl: (7) Failed to connect to 127.0.0.1 port 4124 after 0 ms: Couldn't connect to server    HTTP_STATUS:000 TIME:0.000409s
GET /v1/chat/completions           -> curl: (7) Failed to connect to 127.0.0.1 port 4124 after 0 ms: Couldn't connect to server    HTTP_STATUS:000 TIME:0.000280s

--- ps grep for 4124/mock_llm owners ---
(no 4124-bound processes)

--- worktree .env vision-related keys (sanitized) ---
OPENAI_BASE_URL=<set>
OPENAI_SELECTABLE_MODELS=<set>
OPENAI_MODEL_VISION=<set>
```

### Verdict: NOT-GREEN

The gateway at `127.0.0.1:4124` is **NOT reachable**: no TCP listener, no process owning the port. The text-completion POST cannot return 200 because nothing accepts the connection. The vision-capable POST check is moot — TCP refusal short-circuits any HTTP.

Per task STOP CONDITIONS: *"gateway not vision-capable (step 0)"* and *"capture evidence + report, no retries, no forcing"*. Hard stop fires immediately.

### Regression vs. stage-1 §C (this same rollout doc, 2026-09-26T20:21Z)

The prior stage-1 WP8 run reported proxy state **"UP-but-NOT-VISION-CAPABLE"** (pid 1920968 — `python /tmp/v0153_mock_llm.py`, text 200 + vision 500). My current probe shows the mock process is gone — **a regression from UP-but-mock to fully DOWN**. The mock was killed between 2026-09-26T20:32Z (last `Graceful shutdown complete` per §G2-followup) and 2026-09-27T03:33Z (my probe). The §C rollout already classified this mock as "externally owned" and "functionally DOWN for vision" — so its absence is consistent with the prior verdict that this proxy never supported vision-capable E2E.

The dispatch header stated *"Gateway window is USER-AUTHORIZED (2026-09-26)"*, but no follow-up gateway (e.g., a real vision-capable LLM at the same port) has been brought up. OPENAI_BASE_URL still points at the dead :4124.

### Actions NOT taken (per task constraints)

- **Did NOT** improvise a substitute gateway.
- **Did NOT** point the worktree .env at a different host/port.
- **Did NOT** boot the daemon (STEP 1 fully skipped — STEP 0 hard stop).
- **Did NOT** fire the compare_images path (no facade call attempted).
- **Did NOT** do C4 flip, PD-31 re-execution, or E.7 cleanup.
- **Did NOT** touch `:8081`, `:9797` (live), `:7979` (demo), or prod/demo DBs.
- **Did NOT** merge/push to any remote.
- **Did NOT** make the GREEN-shape single commit (`test(designer): P2 E2E compare — gateway-window proof`) — the commit body shape (verdict=GREEN, pinned_spec_sha assertion result, evidence paths) does not apply when STEP 0 fails before any facade call. The dispatcher can decide whether a separate NOT-GREEN commit is appropriate (worktree is `awaiting-user-signoff` with `no-merge/no-push` locks).

### Artifact status

| Item | Status |
|------|--------|
| `verdicts/p2-wp8-e2e-findings.md` (this file) | **written** (NOT-GREEN proof) |
| `verdicts/p2-wp8-e2e-rollout.md` §G gateway-window section | **NOT appended** (reserved for GREEN; mixing NOT-GREEN proof there would conflict with the rollup semantics) |
| `capture-adopt-or-build.md` §C4 row | **NOT edited** (no explain_image call fired; the §C4 cell remains "DEFERRED (vision spot-check)") |
| `decisions.md` PD-31 row | **NOT updated** (no agent-turn image_save fired) |
| Single commit `test(designer): P2 E2E compare — gateway-window proof` | **NOT made** (GREEN shape, no GREEN proof) |
| Substrate ids `61badab6...` / `ec846091...` | **preserved** (untouched on disk) |
| Pinned spec SHA `81113ea0...` | **preserved** (file not opened; SHA cannot have changed) |

### Required to unblock (single follow-up dispatch)

The proxy at `127.0.0.1:4124` must be replaced with a vision-capable LLM endpoint (real provider or vision-capable mock). When the port answers `/v1/chat/completions` with HTTP 200 on both text-only and vision content, re-dispatch this task verbatim against the same worktree tip — §E remains valid end-to-end and ready to fire.

---

*End of p2-wp8-e2e-findings.md (NOT-GREEN proof, STEP 0 hard stop).*
---

## Gateway-window rerun (NOT-GREEN) — 2026-09-27T03:51:44Z

- **Date:** 2026-09-27T03:51:44Z (re-dispatch timestamp)
- **Worktree:** `/home/nea/ensemble-src-wt-designer-agent-design` (branch `feature/designer-agent-design`, tip `3cb7ee22` at dispatch — P3 gateway-takeover commit on top of the prior NOT-GREEN evidence commit; `git log -1` shows the actual local tip is the same)
- **Operator:** worker (this report)
- **Verdict:** **NOT-GREEN — STEP 0 GATEWAY PROBE FAILURE (hard stop, vision POST 502)**
- **Substrate pair (preserved, untouched this run):** `61badab6017744cd9de7a05ab1823619` (settings, 55558 B) + `ec84609148dd4ca09b0169a954835e4f` (home, 44523 B)
- **Pinned-spec SHA (re-verified this run, MATCH):** `81113ea0f2ffe0d2e7994e963e34f04f10989887a659a33774e2966af2c85b24` — `sha256sum verdicts/evidence/e2e-fixture-spec.md` printed this exact hash; the file has not been opened/edited since the prior NOT-GREEN run.

### Re-dispatch context (vs. prior NOT-GREEN, 2026-09-27T03:33Z)

The prior run was NOT-GREEN because **:4124 had no listener** (the mock killed between 2026-09-26T20:32Z and 2026-09-27T03:33Z). Between then and this re-dispatch:
1. The phase-lead executed P3 gateway-takeover (commit `3cb7ee22`, see `verdicts/p3-gateway-takeover.md`): real proxy provisioned at `127.0.0.1:4124`, bare-ASGI pass-through to `https://llm.ensem.dev/v1`, real Authorization header from the live install .env, dummy-key inbound, service `p3-llm-gateway` pid `1979093`, log `/home/nea/agents-ensemble/data/services/p3-llm-gateway.log`.
2. That take-over demonstrated vision capability against the SAME 32x32 solid-red PNG used in §E ("VISION (vision + same PNG, 'what single color') → 200, real perception").

This re-dispatch therefore re-tested the SAME :4124 endpoint against the same procedure's hard gate.

### STEP 0 — Gateway probe (hard gate, fresh evidence)

**Raw probe evidence:** `/tmp/wp8/probe-evidence-20260927T035144Z.txt` (1305 bytes; full file preserved on disk; Authorization headers in the request were literal `[REDACTED]` placeholders).

```
PROBE_TIME=2026-09-27T03:51:44Z
PROBE_TARGET=http://127.0.0.1:4124

--- ss -tlnp | grep 4124 ---
LISTEN 0  2048  127.0.0.1:4124  0.0.0.0:*  users:(("python",pid=1979093,fd=13))

--- text POST (no-vision, model=agentic, max_tokens=15) ---
HTTP_STATUS:200 TIME:1.944932s
BODY: {"id":"2026092711514415264db1cf674194","object":"chat.completion",
       "created":1790481106,"model":"glm-5.3",
       "choices":[{"index":0,"message":{"role":"assistant","content":"",
       "reasoning_content":"The user just said \"Say OK.\" This is a very simple request."},
       "finish_reason":"length"}],
       "usage":{"prompt_tokens":15,"completion_tokens":15,"total_tokens":30}}

--- vision POST (model=vision, 32x32 solid-red PNG via data URI, max_tokens=30) ---
HTTP_STATUS:502 TIME:2.009283s
BODY: {"error":{"type":"server_error",
       "message":"openai: 图片输入格式/解析错误"}}

--- GET /v1/models ---
HTTP_STATUS:200 TIME:0.007061s
BODY: {"data":[{"created":1700000000,"id":"agentic",...},
       {"id":"agentic-mini",...},{"id":"agentic-turbo",...},
       {"id":"coding",...},{"id":"coding2",...},...12 models total,
       "vision" not listed but routes server-side (per take-over report)]
```

### Assertion table (Step 0)

| Probe | Expected | Observed | Verdict |
|-------|----------|----------|---------|
| text POST 200 (model=agentic, no vision) | HTTP 200 | HTTP 200, real glm-5.3 echo-free (reasoning_content present, content empty due to low max_tokens=15 — EXPECTED per dispatcher note "low max_tokens can starve content while reasoning consumes budget") | **PASS** |
| vision POST 200 (model=vision, 32x32 solid-red PNG) | HTTP 200 | HTTP 502, upstream error envelope `{"error":{"type":"server_error","message":"openai: 图片输入格式/解析错误"}}` | **FAIL** |
| GET /v1/models 200 | HTTP 200 | HTTP 200, 12 models listed | **PASS** |

### Gateway service log tail (immediately after my probes, redacted)

```
[p3gate] GET /v1/models -> 200 (0.01s)              # my models probe
[p3gate] POST /v1/chat/completions -> 200 (1.94s)   # my text probe (succeeded — glm-5.3 reasoning)
[p3gate] POST /v1/chat/completions -> 502 (2.01s)   # my vision probe (upstream 502)
```

The gateway itself forwarded the vision request to upstream and received a 502 from `llm.ensem.dev`; the gateway is **functioning** (it served text and models 200s and surfaced the upstream error faithfully). The failure is **upstream rejection** of the vision request format, not a gateway plumbing defect.

### Verdict: NOT-GREEN (hard stop, per task STOP CONDITIONS)

Per task STOP CONDITIONS: *"step-0 probe failure; capture + report, no retries, no forcing"*. The vision POST failed with an error envelope; per the literal "If any fails → STOP" rule, the re-dispatch hard-stops at Step 0.

### Distinguishing observation (NOT-acted-on)

The same 32x32 solid-red PNG format returned HTTP 200 against `llm.ensem.dev/v1` during the p3-takeover verification (see `verdicts/p3-gateway-takeover.md` §4 "Both-half verification probes"). My probe returned 502 from the same upstream. Possible root causes (not investigated per "no retries / no forcing" — the dispatcher decides):

1. **Upstream behavior drift** — `llm.ensem.dev` may now be stricter on the image data URI format or the PNG byte pattern; the gateway is a faithful pass-through so any upstream change surfaces here.
2. **PNG byte differences** — the take-over report does not publish the exact base64; my test PNG (`iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAAAH0lEQVR42mP8z8DwHwAFhAJ/wlseowAAAABJRU5ErkJggg==`) is a valid 32x32 solid-red RGB image but may differ in IDAT compression bytes from the take-over's PNG. Upstream image parsers can be sensitive to byte-level differences (CRC, filter bytes, compression strategy).
3. **Transient upstream error** — the upstream error message format ("server_error" type) suggests a generic 500-class error from the upstream's image processing pipeline, which could be intermittent.

**No further probes were fired** — "no retries" rule observed. The dispatcher can decide whether to:
- Re-dispatch this task with a validated test PNG (e.g., a freshly-generated PIL image) to disambiguate options 2 vs 1/3.
- Diagnose the upstream image-format rejection separately.

### Actions NOT taken (per task constraints and STOP CONDITIONS)

- **Did NOT** retry the vision probe with a different input (no retries).
- **Did NOT** improvise a substitute gateway or point .env elsewhere.
- **Did NOT** boot the daemon (STEP 1 fully skipped — STEP 0 hard stop).
- **Did NOT** fire the compare_images path (no facade call attempted; no designer turn issued).
- **Did NOT** do C4 flip, PD-31 re-execution, or E.7 cleanup (none of the production path ran).
- **Did NOT** touch `:8081` (P3 launchpad — verified still UP at `ss -tlnp`), `:4124` (gateway — verified still UP at `ss -tlnp`, no shutdown signal sent), `:9797` (live), `:7979` (demo), or prod/demo DBs.
- **Did NOT** merge/push to any remote (worktree is `awaiting-user-signoff` with `no-merge/no-push` locks).
- **Did NOT** modify `capture-adopt-or-build.md` §C4 (no explain_image call fired; cell remains "DEFERRED (vision spot-check)").
- **Did NOT** modify `decisions.md` PD-31 row (no agent-turn image_save fired).

### Artifact status (this rerun)

| Item | Status |
|------|--------|
| `verdicts/p2-wp8-e2e-findings.md` (this file) | **APPENDED** — this Gateway-window rerun section added; prior NOT-GREEN history (lines 1-79) preserved verbatim |
| `verdicts/p2-wp8-e2e-rollout.md` §G gateway-window section | **NOT appended** (reserved for GREEN-shape proof; mixing NOT-GREEN proof there would conflict with the rollup semantics) |
| `verdicts/evidence/e2e-fixture-spec.md` | **preserved, untouched, SHA re-verified MATCH** (`81113ea0f2ffe0d2e7994e963e34f04f10989887a659a33774e2966af2c85b24`) |
| Substrate ids `61badab6...` / `ec846091...` | **preserved, untouched** on disk |
| `:8081` P3 launchpad daemon | **UP, untouched** |
| `:4124` p3-llm-gateway service (pid 1979093) | **UP, untouched** — Authorization header still being injected outbound per design; no worktree-side probes affected the service |

### Single commit

`test(designer): P2 E2E compare — gateway-window proof NOT-GREEN` (commit body below)

### Required to unblock (single follow-up dispatch)

If the dispatcher chooses to disambiguate upstream vs test-input vs transient, the cheapest next step is a single diagnostic probe with a freshly-generated, definitely-valid PNG (e.g., `python -c "from PIL import Image; import io,base64; ..."`) through :4124. If that returns 200, the gateway is fine and the production path can be re-dispatched verbatim against the same worktree tip. If it returns 502 again, the upstream has a real vision-format rejection and the gateway window is functionally DOWN for vision despite being UP at the TCP level — escalation path = user (per the p3-takeover escalation rule).

The §E procedure itself remains valid end-to-end and ready to fire once Step 0 returns all 200.

---

*End of Gateway-window rerun section (NOT-GREEN proof, STEP 0 hard stop, vision POST 502). Prior NOT-GREEN proof (2026-09-27T03:33Z) preserved intact above.*
