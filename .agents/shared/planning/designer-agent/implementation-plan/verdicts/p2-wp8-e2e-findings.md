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