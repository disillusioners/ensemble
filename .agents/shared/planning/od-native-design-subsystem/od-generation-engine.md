# Open Design Generation Engine — Deep-Dive Map & Failure Analysis

> **Commission:** read-only deep-dive of the OD generation engine feeding (a) the architecture decision on replacing the OD MCP dependency with an ensemble-native design subsystem, and (b) root-cause input for the 2026-10-06 live 2/2 `od_generate_design` failure.
> **Evidence bases:** OD monorepo `~/opt/open-design` @ `53231d40` (clean tree, = remote `main` tip; daemon `dist/` built Oct 2 04:02 from this tree), MCP package `open-design-mcp@0.16.1` at `/home/nea/services/opendesign/node_modules/open-design-mcp` (npm `latest`), live daemon journal (`journalctl --user -u opendesign-daemon`), smoke evidence `SMOKE-STATUS.md`.
> **Method:** all paths read directly; every claim carries `file:line`. Hypotheses are labeled.

---

## 0. Executive summary

The "OD generation engine" behind `od_generate_design` is **two thin components and a dumb pipe**:

1. **`open-design-mcp@0.16.1`** (separate npm package, NOT in the OD monorepo) composes a ~large system prompt **client-side** from a **vendored snapshot of OD's prompt library from 2026-05-17** — 4.5 months stale — and POSTs `{baseUrl, apiKey, model, systemPrompt, messages, maxTokens}` to the daemon.
2. **The OD daemon `/api/proxy/openai/stream` route** is a **blind SSE relay**: it forwards `max_tokens` verbatim, streams only `choices[0].delta.content` (reasoning deltas and `usage` frames are **dropped**), **never inspects `finish_reason`**, and emits a clean `end` event on `[DONE]` **or on any upstream stream close, with or without `[DONE]`**.
3. **The MCP tool accumulates the string** and returns it. On a clean `end` it returns whatever it has — partial HTML, zero HTML, thinking-prose — **with no `isError` and no partial marker**. The partial marker exists **only** on the MCP client's own abort/timeout path.

Consequently, **token-budget exhaustion (`finish_reason=length`) after heavy in-content thinking is completely indistinguishable from success** at every layer. The 2/2 live failure is fully consistent with `max_tokens` exhaustion (latency ≈ budget ÷ stream-rate); the stale vendored discovery prompt (which *mandates* a turn-1 question-form + STOP, contradicting the complete brief) is a strong amplifier that explains why more budget produced *less* artifact. The daemon journal confirms both live calls ended with **no upstream HTTP error and no internal error** — a "normal" stream end both times.

---

## 1. Q1 — End-to-end trace of `od_generate_design`

Two components; the MCP tool definition is **not** in the OD monorepo.

### Component A — MCP server (`open-design-mcp@0.16.1`)

| Step | File | Evidence |
|---|---|---|
| Tool registration | `dist/src/tools/generate-design.js:210-218` | `server.registerTool('od_generate_design', …)`; input schema `prompt`, `projectId`, `kind` (default `prototype`), `userInstructions`, `projectInstructions`, `maxTokens` (int 1–200000, **default 64000**) |
| BYOK config load | `dist/src/tools/generate-design.js:59-81` + `dist/src/config.js:13-20` | lazy `getByokConfig()` → `BYOK_BASE_URL`/`BYOK_API_KEY`/`BYOK_MODEL` required, `BYOK_PROVIDER` enum default **`openai`** |
| customInstructions fetch | `generate-design.js:82-101` | if `projectId`: `GET /api/projects/{id}`; precedence `metadata.customInstructions` → top-level `customInstructions` (:93-96) |
| merge instructions | `generate-design.js:48-56,102` | `mergeProjectInstructions(stored, perCall)` = `stored + "\n\n---\n\n" + perCall` |
| system prompt compose | `generate-design.js:104-115` | `composeSystemPrompt({ metadata: { kind }, userInstructions, projectInstructions, streamFormat: 'plain' })` — see §2 |
| proxy request | `generate-design.js:117-126` | `{ baseUrl, apiKey, model, systemPrompt, maxTokens, messages: [{role:'user', content: prompt}] }` |
| timeout signal | `generate-design.js:127-131` + `config.js:8-11` | `AbortSignal.timeout(OD_GENERATE_TIMEOUT_MS)` — **default 600 000 ms (10 min)** |
| HTTP POST | `dist/src/od-client.js:61-74` | `POST ${OD_DAEMON_URL}/api/proxy/${provider}/stream`, `accept: text/event-stream` |
| stream accumulate | `generate-design.js:151-180` + `dist/src/sse-parser.js:67-94` | accumulates `delta` events into a string; `error` event → `isError` result; `end` → break |
| return | `generate-design.js:205-207` | on clean `end`: `{ content: [{text: accumulated}] }` — **no isError regardless of content state** |
| abort path | `generate-design.js:182-204` | only on AbortError with `accumulated.length > 0`: appends `<!-- Generation … Output is incomplete. -->` marker + `isError: true` |
| server bootstrap | `dist/src/server.js` (head) | stdio `McpServer`, `registerAllTools(server, client, core.OD_GENERATE_TIMEOUT_MS)` |

**There is no artifact persistence in this flow.** The tool returns text to the calling agent; persistence only happens if the caller invokes `od_save_artifact` (`POST /api/artifacts/save`) or `od_save_project_file` (`POST /api/projects/{id}/files`) separately. In the failed smoke both were correctly skipped per protocol (SMOKE-STATUS.md §4).

### Component B — OD daemon proxy (`~/opt/open-design/apps/daemon`)

| Step | File | Evidence |
|---|---|---|
| route | `src/routes/chat.ts:1032` | `app.post('/api/proxy/openai/stream', …)` |
| body | `chat.ts:1033-1045` | destructures `baseUrl, apiKey, model, systemPrompt, messages, maxTokens`; 400 if base trio missing |
| SSRF check | `chat.ts:1047-1055` + `connectionTest.ts` | `validateExternalApiBaseUrl` (our allowlist entry `llm.ensem.dev` via `OD_ALLOWED_INTERNAL_HOSTS`, systemd unit) |
| reasoning-egress gate | `chat.ts:1056-1063` + `src/reasoning-egress.ts:55-61` | `policyMode(undefined) → 'enabled'` — MCP never sends `reasoningExecution`; never denies |
| URL | `chat.ts:1065` + `integrations/provider-models.ts:31-39` | `appendVersionedApiPath(baseUrl, '/chat/completions')` — appends `/v1` unless a `/vN` path segment exists |
| payload | `chat.ts:1070-1082` | `systemPrompt` unshifted as `{role:'system'}`; `effectiveMaxTokens = maxTokens>0 ? maxTokens : 8192`; `{ model, messages, …buildOpenAIChatTokenParam(model, effectiveMaxTokens), stream: true }` |
| upstream fetch | `chat.ts:1093-1116` | Bearer auth; OpenRouter referer headers if openrouter; `redirect:'error'`; `clientDisconnectSignal` aborts on client close (:732-739) |
| Azure retry | `chat.ts:1083-1091,1118-1133` | 400 unsupported-max_tokens → retry with `max_completion_tokens` (Azure hostnames only) |
| stream relay | `chat.ts:1147-1173` + `streamUpstreamSse` :553-583 | per SSE frame: `[DONE]` → `end`; `data.error` → `error` event; `extractOpenAIText(data)` non-empty → `guard.sendDelta(delta)` |
| **clean-end fallback** | `chat.ts:1173` | `if (!ended) sse.send('end', {})` — **upstream close without `[DONE]` also ends "successfully"** |
| internal error | `chat.ts:1175-1178` | fetch/parse exception → `error` SSE + log `[proxy:openai] internal error:` |
| SSE wrapper | `src/server.ts:3017-3058` | keepalive comment frames every 25s (`SSE_KEEPALIVE_INTERVAL_MS`, server.ts:1807); **no timeout, no byte cap** |
| request log | `chat.ts:1066-1068` | `[proxy:openai] POST <host> model=<model>` — the **only** per-request daemon log (confirmed in journal, §9) |

### Component C — persistence routes (only when caller saves)

- `POST /api/artifacts/save` — `src/routes/project/index.ts:5740-5761`: writes `html` verbatim to `RUNTIME_DATA_DIR/artifacts/<stamp>-<slug>/index.html` (server.ts:1416), runs `lintArtifact`, returns `{path,url,lint}`. **No extraction, no fence stripping, no tag balancing.**
- `POST /api/artifacts/lint` — `project/index.ts:5768-5783` → `src/lint-artifact.ts` — "deliberately greppy… does **NOT** parse HTML" (lint-artifact.ts:20-22) → **cannot detect truncation** (matches the smoke's void lint verdict, SMOKE-STATUS.md §3).

### Sibling engine (architecture context, not used by `od_generate_design`)

The daemon's *native* generation lane (OD UI "runs", MCP `start_run`) is a different, heavier engine: agent-based via `src/runtimes/` (claude-stream, codex, byok-opencode …), `DEFAULT_OUTPUT_TOKEN_LIMIT = 16_384` (`src/runtimes/byok-opencode.ts:10`), GenUI registry `src/genui/`, runs routes `src/routes/runs.ts`. The `od_generate_design` BYOK lane shares none of it except the projects store.

---

## 2. Q2 — Prompt assembly

### Where the prompts live

- **MCP side (authoritative for `od_generate_design`):** vendored copy inside the MCP package at `dist/vendor/od-contracts/src/prompts/{system,official-system,discovery,directions,deck-framework,media-contract}.js` — **vendored 2026-05-17 from upstream commit `7766582f`** (`vendor/od-contracts/VENDORED_FROM.md`). Format: inline TS string constants (now compiled JS).
- **Daemon side (current, NOT used by `od_generate_design`):** `packages/contracts/src/prompts/system.ts` (1183 lines) and `discovery.ts` (287 lines) — last modified 2026-09-10 (`git log`); includes the **OD Next strategy** (default since 7320f9b8, 2026-08-24) and a **rewritten RULE 1** (see §2.3).

### Composition order (`composeSystemPrompt`, vendored `system.js:57-164`; call site `generate-design.js:106-111`)

1. `API_MODE_OVERRIDE` (vendored `system.js:180-196`) — top-anchored: "no tools wired"; forbids pseudo-tool markup; **allows** plain prose plans and a final `<artifact type="text/html">` block; **explicitly allows `<question-form>` for discovery on turn 1** (:194).
2. `SKIP_DISCOVERY_BRIEF_OVERRIDE` — only if `metadata.skipDiscoveryBrief === true` (:76-79). **`od_generate_design` passes only `{ kind }` (`generate-design.js:107`) → this override never fires.**
3. `DISCOVERY_AND_PHILOSOPHY` + identity charter + `OFFICIAL_DESIGNER_PROMPT` (:80).
4. `userInstructions` (:91-93), then `projectInstructions` (stored+per-call merged, :94-96).
5. metadata block (`renderMetadataBlock`, :197-…): prints `kind`, and for prototype/template/other emits `**platform**: (unknown — ask: …)` with "**you MUST include a matching question in your turn-1 discovery form**" (:202-210) — **the MCP only ever passes `kind`, so this "ask" line is always present.**
6. deck framework / media contract for matching kinds (:133-159).

### How the Turn1/Turn2/Turn3 brief enters

`od_compose_brief` (`dist/src/tools/compose-brief.js:19-64`) is a pure formatter: `[brief answers]…`, `[brand spec]…`, `[page brief]…` sections joined with blank lines. Its output is **not** merged into the system prompt — the **caller** passes it as the `prompt` argument, which becomes the single `user` message (`generate-design.js:123-125`). So the "designed skip" of discovery is expressed only inside the user message, while the system prompt still mandates the discovery arc — a structural conflict (§ H3).

### 2.3 The prompt drift (load-bearing for the failure + the architecture decision)

| | Vendored (May 17) — what `od_generate_design` sends | Current upstream (Sep 10) — what OD's own UI sends |
|---|---|---|
| RULE 1 | "**turn 1 must emit a `<question-form id=\"discovery\">` … very first output … Nothing else. … No extended thinking.**" (vendored `discovery.js:32-34`, arc at :6-18) | "**clarify only unresolved material requirements … If they provide enough information … skip the form and proceed directly to RULE 2 / RULE 3.**" (`packages/contracts/src/prompts/discovery.ts:37-41`) |
| discovery skip | only via `skipDiscoveryBrief: true` metadata (not set by the MCP) | same mechanism, plus the rewritten RULE 1 makes complete briefs skip naturally |

Upstream **rewrote the discovery contract to solve exactly the contradiction the MCP lane still has** — but the fix lives in the monorepo, and the MCP vendors the old snapshot. `open-design-mcp@0.16.1` is npm-`latest` (registry dist-tags checked 2026-10-06) — **there is no newer MCP to upgrade to; the drift is unfixable by version bump.**

---

## 3. Q3 — Model call shape

Outbound request (daemon → upstream), from `chat.ts:1070-1082` + `integrations/openai-chat-token-params.ts`:

```jsonc
POST {BYOK_BASE_URL}/v1/chat/completions      // /v1 appended iff absent (provider-models.ts:31-39)
Authorization: Bearer {BYOK_API_KEY}
{
  "model": "vision",                          // BYOK_MODEL, verbatim
  "messages": [ {"role":"system","content":<composed system prompt>},
                {"role":"user","content":<prompt arg>} ],
  "max_tokens": 50000 | 64000,                // see mapping below
  "stream": true
}
```

- **maxTokens mapping** (`openai-chat-token-params.ts:1-22`): `max_completion_tokens` **only** for models matching `gpt-5*`, `o1*`, `o3*`, `o4*`; everything else — including `vision` — gets plain **`max_tokens`**, forwarded **verbatim** (daemon default 8192 fires only if a client sends no/invalid number, `chat.ts:1075-1076`; the MCP always sends one). Azure-only 400-retry swaps to `max_completion_tokens` (`chat.ts:1118-1133`).
- **temperature: absent** (not set anywhere in the payload).
- **reasoning/thinking fields: absent.** Greps for `reasoning_content|reasoning_tokens|<think>|reasoning_effort|enable_thinking|include_reasoning` across the proxy path and the entire MCP dist: **zero hits**. No reasoning is requested, suppressed, or configured — the model's thinking behavior is entirely whatever the upstream (`llm-supervisor-proxy` → `vision`) does by default.

---

## 4. Q4 — Thinking & token accounting (the crux)

### Stream parser treatment of reasoning

- **Daemon → MCP:** `extractOpenAIText` (`chat.ts:625-632`) reads **only** `choices[0].delta.content` (or legacy `choices[0].text`). `delta.reasoning_content` and any other delta fields are **silently dropped**. `usage` frames (which carry `reasoning_tokens`/`completion_tokens`) are **never parsed**. `finish_reason` is **never inspected anywhere in the openai proxy path** (grep-verified) — `finish_reason=length` and `finish_reason=stop` are indistinguishable downstream.
- **`<think>` blocks:** no handling anywhere; if a model emits them they ride inside `content` and are passed through.
- **MCP accumulate:** `accumulated += evt.delta` (`generate-design.js:157-159`) — unbounded; no counting, no cap, no reasoning separation. Whatever the tool result showed **must have come through `delta.content`** — i.e., attempt 2's visible "thinking prose" was *content-channel* output of the upstream model, not `reasoning_content`.

### Every code path to (a) truncated-mid-CSS partial with NO isError / NO marker

1. **`finish_reason=length` then `[DONE]`** (token-budget exhaustion): upstream emits final content delta + `[DONE]`; daemon sends `end` (chat.ts:1150-1153); MCP breaks and returns the partial string **clean** (generate-design.js:176-177, 205-207). ✔ matches A1 byte-for-byte (mid-CSS cut, no marker).
2. **Upstream closes the stream without `[DONE]`** (connection cut, upstream abort, idle timeout on any hop): `streamUpstreamSse` loop exits on `done` (chat.ts:558-560), `ended` stays false, daemon sends a **clean** `end` (chat.ts:1173) → same clean partial. ✔ also matches A1.
3. **Role-marker guard contamination** (`chat.ts:1148,1162-1169`; `src/role-marker-guard.ts:81-82`): a line-start `## user|assistant|assist|system` marker truncates the stream — **but always appends a "⚠️ Security warning … truncated" delta** (chat.ts:686-692). **Ruled out for both attempts: the warning text is absent from both tails.**
4. **MCP-side abort/timeout** — produces the marker + `isError:true` (generate-design.js:183-201). **Ruled out: no marker, no isError, both calls ≪ 600 s.**

### Every code path to (b) zero-content / thinking-only result with no error

1. **All completion tokens spent on content-channel thinking before any artifact**: `max_tokens` exhausted mid-thinking → `finish_reason=length` + `[DONE]` → clean `end` → MCP returns the thinking-prose string as the "result" (A2's exact symptom — tool result was non-empty prose, no HTML).
2. **All tokens in `reasoning_content`** (if the upstream splits channels): those deltas are **dropped** by `extractOpenAIText`; if the model produces *only* reasoning before `length`, `accumulated === ''` and the tool returns an **empty string, success** (generate-design.js:205-207). Same shape as A2 if the vision proxy merges reasoning into content (which A2's visible prose proves it does, at least partially).
3. Malformed delta JSON dropped by MCP parser (sse-parser.js:39-42, stderr note only) — would degrade content silently but cannot alone zero it.

### Can reasoning exhaust the budget while content stays empty?

**Yes, by construction, in two ways:** (i) if the provider counts reasoning toward `max_tokens` (o-series-style joint budget) and the model never exits its thinking phase, `length` fires with zero content deltas forwarded; (ii) even without a reasoning channel, a model that "thinks in prose" (A2's observed behavior) spends the same `max_tokens` budget on non-artifact text. **The pipeline cannot distinguish either from success** — there is no finish_reason, no usage accounting, no minimum-content check anywhere in the chain (all grep-verified).

---

## 5. Q5 — Caps, timeouts, truncation; artifact state per path

| Mechanism | Where | Value | Resulting artifact state |
|---|---|---|---|
| `max_tokens` (completion cap) | chat.ts:1075-1082 | MCP-supplied (50000 / 64000; schema default 64000, ceiling 200000) | `length` → **silent partial or silent empty** (see §4) |
| daemon fallback cap | chat.ts:1075-1076 | 8192 — only when caller omits maxTokens | same silent behavior; **not our case** (MCP always sends) |
| MCP client timeout | config.js:11 + generate-design.js:128 | `OD_GENERATE_TIMEOUT_MS`, default 600 000 | abort → **partial + marker + isError** (not observed) |
| daemon SSE keepalive | server.ts:1807,3037-3040 | 25 s comment frames | keeps connection open; **no cap** |
| daemon-side generation timeout | — | **none** in the proxy path | stream runs as long as upstream runs |
| upstream fetch abort | chat.ts:732-739 | client disconnect only | unwinds silently (client already gone) |
| content-length cap | — | **none anywhere**; accumulation unbounded | refutes the smoke's "hidden ~16K backend cap" hypothesis (SMOKE-STATUS.md §7.1.2) |
| role-marker guard | role-marker-guard.ts | kill-on-detection | partial + ⚠️ warning appended (absent here → not implicated) |
| upstream HTTP error | chat.ts:1118-1145 | 4xx/5xx | `error` SSE → MCP `isError` (journal would log `upstream error:` — **absent**) |
| internal exception | chat.ts:1175-1178 | any throw | `error` SSE → MCP `isError` (journal would log `internal error:` — **absent**) |

**Why both failures look clean:** every truncation path that actually fired ends in the SSE `end` event, and the MCP treats `end` as unconditional success (generate-design.js:176-177 → 205-207). The only no-error-yet-truncated paths are exactly (budget exhaustion | upstream clean close), both of which the journal's silence supports.

---

## 6. Q6 — Done-detection & post-processing

- **Done-detection:** none beyond the SSE `end` event. No `</html>` check, no completeness probe, no `finish_reason`, no `usage` read.
- **HTML extraction:** none. The composed prompt asks for a `<artifact type="text/html">` wrapper (vendored system.js:193), but the MCP returns the **raw accumulated string** — fence/artifact extraction is the calling agent's job. (Attempt 1's partial began with `<!doctype html>` — the model apparently skipped the wrapper.)
- **Validation before save:** `POST /api/artifacts/save` writes bytes verbatim then lints (project/index.ts:5751-5757); the linter is explicitly non-parsing (lint-artifact.ts:20-22) → **structural truncation is invisible to it** (observed: 1×P1 verdict on a non-renderable partial).

---

## 7. Q7 — Upstream delta (`53231d40` → origin/main / v0.24.1)

- `git fetch origin` succeeded. **Remote `main` == `53231d40`** — the clone is at the tip of main; there is no newer engine code on main.
- Tags: repo uses `open-design-v*`; fetched `open-design-v0.24.1` (`89e64d81`). It is a **release branch cut 2026-09-23/24, i.e. *before* our HEAD (Sep 30)**; `git merge-base --is-ancestor` confirms it does **not** descend from `53231d40`.
- `git log 53231d40..v0.24.1` = 13 commits: billing (Coding Plan usage/wallet), CMS campaigns, telemetry classification, macOS packaged runtime, diagnostics consent (OPEND-3397), release chores. `git diff --stat 53231d40 v0.24.1 -- apps/daemon/src packages/contracts/src` touches **only diagnostics files** — **zero changes to `routes/chat.ts` (proxy), prompts, or request params.**
- CHANGELOG.md (clone HEAD): nothing about thinking/zero-output/truncation in the proxy; only a historical Codex-runtime note ("Clamp Codex reasoning effort", #223, CHANGELOG.md:1114).
- **Verdict: no upstream fix exists for this failure class — neither on main nor in v0.24.1.** The one *relevant* upstream change (the RULE 1 discovery rewrite, landed ≤ 2026-09-10) is prompt-side, already in our clone, and **not consumed by the MCP lane** (§2.3).

---

## 8. Q8 — Installed versions (read-only)

- **OD daemon:** running, healthy — `GET /api/health` → `{"ok":true,"version":"0.23.1"}`; `GET /api/version` → `0.23.1, channel: development, packaged: false`. Runs from this checkout via systemd (`~/.config/systemd/user/opendesign-daemon.service`: `node apps/daemon/dist/cli.js`, `WorkingDirectory=/home/nea/opt/open-design`, port 7456 loopback; dist built Oct 2 04:02 from the clean tree @53231d40).
- **MCP:** `open-design-mcp@0.16.1` (npm wrapper `/home/nea/services/opendesign/package.json` dep `^0.16.1`); registry `latest` = 0.16.1.

---

## 9. Live-failure evidence from the daemon journal

```
Oct 06 10:53:04 … [proxy:openai] POST llm.ensem.dev model=vision   ← Attempt 1
Oct 06 11:04:40 … [proxy:openai] POST llm.ensem.dev model=vision   ← Attempt 2
```

Only 2 lines for the whole unit-day: **no `upstream error:`, no `internal error:`, no restarts.** Both streams therefore opened OK, relayed, and ended via a normal completion path. The wall-clock gap between call start and the smoke's 130–170 s observation ≈ `max_tokens ÷ stream rate` (50 K ÷ ~330 tok/s ≈ 150 s; 64 K ÷ ~450 tok/s ≈ 140 s) — the latency is *budget-shaped*, not timeout-shaped (a fixed idle/hard timeout would fire at a constant time, and the Oct 3 dev-lane success ran 167 s without a cut).

---

## 10. Ranked root-cause hypotheses (2/2 live failure)

### H1 🟢 — `max_tokens` exhaustion with content-channel thinking (mechanism)
`max_tokens` (50000/64000) covers thinking+output together in the `content` channel of the `vision` model. A1 exhausted mid-CSS; A2 exhausted mid-thinking before any HTML. `finish_reason=length` is invisible to the whole chain (§4), so both ends look like success.
**Evidence:** latency-budget correlation (§9); journal silence on errors (§9); reasoning-blind, finish_reason-blind chain (§4, all grep-verified); A2's result *was* thinking prose through `delta.content` (only path to the tool result); "no error, no marker, no timeout" is the exact signature of the clean-`end` return (generate-design.js:205-207).
**Confirm:** llm-supervisor-proxy request logs/usage for 2026-10-06 10:53:04Z & 11:04:40Z — expect `finish_reason=length`, `completion_tokens ≈ 50000/64000`. Or a read-only replay with `maxTokens=8000` + trivial brief → predict fast, small, cut-off output.
**Refute:** proxy logs showing `finish_reason=stop` with `completion_tokens ≪ max_tokens`.

### H2 🟡 — Upstream stream closed without `[DONE]` (alternative mechanism, same signature)
Any hop (llm-supervisor-proxy, ingress) closing the SSE mid-stream yields a **clean** `end` at the daemon (chat.ts:1173) → identical silent partial. OD-side logs cannot distinguish this from H1.
**Evidence:** the code path (§4 path a-2); both attempts' clean ends.
**Confirm/refute:** proxy access logs (connection abort vs completed response); an ensemble-side replay with token accounting on.
Weaker than H1 only because both attempts' durations track their respective budgets (130–170 s scaling with maxTokens), which a fixed cut would not do.

### H3 🟡 — Stale vendored discovery prompt amplifies thinking (why A2 > A1 in badness)
The MCP sends the May-17 prompt whose RULE 1 *mandates* "one prose line + `<question-form>` + STOP" on turn 1 and forbids extended thinking — in direct conflict with the complete Turn-3 brief in the user message, and reinforced by the always-present `platform: (unknown — ask)` metadata line (§2.2). The model visibly wrestles with it (A2 tail: "The system says lead with one short prose line. Let me do that. Actually, given the"). More budget → more non-convergent deliberation, not more artifact — exactly the smoke's refinement #1 (SMOKE-STATUS.md §7.1).
**Evidence:** vendored `discovery.js:32-34` vs `generate-design.js:106-111` (no `skipDiscoveryBrief`); metadata "ask" line vendored `system.js:202-210`; A2 verbatim tail.
**Confirm:** replay same brief with `projectInstructions` prefix "skip discovery, emit the artifact immediately" or (upstream-current prompts) → predict budget still caps output but thinking shrinks sharply.
**Refute:** identical failure with a prompt that has no discovery layer.

### H4 🔴 — Role-marker guard truncation — **ruled out**: would append the ⚠️ warning delta (chat.ts:686-692); absent from both tails.
### H5 🔴 — MCP timeout/abort — **ruled out**: would emit marker + `isError` (generate-design.js:183-201); absent, and both calls ≪ 600 s.
### H6 🔴 — Hidden backend content cap (~16 K, smoke §7.1.2) — **refuted**: no content-length cap exists anywhere in the chain (§5); only token caps, forwarded verbatim.

---

## 11. Architecture-decision notes (observations, not prescriptions)

1. **The OD "engine" is mostly not an engine.** For the BYOK lane, OD contributes: a dumb authenticated SSE relay + prompt text. All intelligence (prompt, accumulation, extraction, validation, persistence) is MCP-side or caller-side. An ensemble-native subsystem would be replacing ~200 lines of relay + a prompt library, not a generation engine. The heavy OD-native agent engine (runtimes/genui, §1 Component C) is not on this path.
2. **The prompt library is the actual asset** — Apache-2.0, in-repo (`packages/contracts/src/prompts/`), current version (Sep) already solves the discovery-skip semantics the vendored copy lacks (§2.3). Vendoring drift is structural: the MCP's copy is a 4.5-month-old snapshot and npm-latest, so **the drift cannot be fixed by upgrading**.
3. **The relay's contract gaps** any native replacement should close: surface `finish_reason` (esp. `length`) and `usage` (incl. `reasoning_tokens`) to the caller; distinguish "upstream closed without `[DONE]`" from `[DONE]`; emit truncation markers at the *daemon/proxy* layer, not only at the client-abort layer; validate artifact completeness (`</html>` / fence balance) before returning; make the linter structural (it is deliberately non-parsing, lint-artifact.ts:20-22).
4. **`reasoning` handling:** the chain is reasoning-blind by omission (no field requested, none parsed, `reasoning_content` dropped). For thinking models this makes budget exhaustion *invisible* — the single biggest observability gap behind this incident.

---

## Appendix — Key file index

| Concern | Path |
|---|---|
| MCP tool def/handler | `~/…/open-design-mcp/dist/src/tools/generate-design.js` |
| MCP brief composer | `…/open-design-mcp/dist/src/tools/compose-brief.js` |
| MCP env/timeout | `…/open-design-mcp/dist/src/config.js:8-20` |
| MCP SSE parser | `…/open-design-mcp/dist/src/sse-parser.js` |
| MCP HTTP client | `…/open-design-mcp/dist/src/od-client.js:61-74` |
| Vendored prompts | `…/open-design-mcp/dist/vendor/od-contracts/src/prompts/*` (from `7766582f`, 2026-05-17) |
| Proxy route | `~/opt/open-design/apps/daemon/src/routes/chat.ts:1032-1182` |
| Text extraction | `chat.ts:625-632` |
| Clean-end fallback | `chat.ts:1173` |
| Token param mapping | `apps/daemon/src/integrations/openai-chat-token-params.ts:1-34` |
| Role-marker guard | `apps/daemon/src/role-marker-guard.ts:81-124` |
| Reasoning-egress gate | `apps/daemon/src/reasoning-egress.ts:55-61` (default-enabled) |
| SSE wrapper/keepalive | `apps/daemon/src/server.ts:1807, 3017-3058` |
| Save/lint routes | `apps/daemon/src/routes/project/index.ts:5740-5783` |
| Linter (non-parsing) | `apps/daemon/src/lint-artifact.ts:15-22` |
| Current prompts (unused by lane) | `packages/contracts/src/prompts/{system,discovery}.ts` |
| systemd unit | `~/.config/systemd/user/opendesign-daemon.service` |
