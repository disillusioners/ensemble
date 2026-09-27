# P3 — LLM Gateway Takeover + Real Provisioning (:4124) — Evidence

**Status: GATEWAY ALIVE — text-200 AND vision-200, REAL backend (no mocks)**
- Date: 2026-09-27 (takeover ~00:00Z)
- Authority: user-authorized disposition A (phase-lead assignment; "real not canned, no substitution")
- Operator: developer[v2] phase-lead (`designer-impl-phase3-lead`)

## 1. Lane investigation (ownership findings)
- `:4124` previously served the upgrade mission's (feature/upgrade-tool-lane-fix, main tree) stage-1 text-only mock — repo-lineage `tests/mock_llm_server.py`; `/tmp/e2e_mock_llm.py` is an ENHANCED COPY of it defaulting to `:4125` (its header says "The original at 4124 is untouched"). Last artifact mtimes: /tmp/e2e-mock-llm.log 2026-09-26 04:42, /tmp/e2e_mock_llm.py 04:47.
- At takeover: NO listener on :4124 (verified repeatedly, connection-refused); NO process matching any mock; running listeners were only 5432 (PG), 7979 (demo daemon), 8081 (p3 daemon), one ephemeral. Demo/live configs point at the REAL gateway (llm.ensem.dev), NOT the mock. Main tree .env still references :4124 but its dev lane (8079) has nothing running.
- Verdict: lane ORPHANED/DEAD — clean takeover, zero running consumers affected. If the upgrade mission later restarts a mock on :4124, the bind will fail loudly on their side (we hold the port); escalation path = user.

## 2. Real backend trace (no canned anything)
- Worktree .env: OPENAI_BASE_URL=http://localhost:4124/v1, OPENAI_MODEL=mock-model, OPENAI_API_KEY=dummy (len 5) — mock-era config, no real credential.
- REAL chain (live 9797 + demo 7979 installs, identical): OPENAI_BASE_URL=https://llm.ensem.dev/v1 (backup https://llm.daoduc.org/v1), OPENAI_MODEL=agentic, OPENAI_MODEL_VISION=vision — the ensemble logical names are REAL gateway model names.
- Real key: lives in the live install .env; read by the proxy at STARTUP ONLY from that file path; injected per-request as Authorization; never logged, never in argv, never in any artifact (this file secret-scanned).

## 3. Provisioning mechanics
- `/tmp/p3gate/gateway.py` (mode 0600, ops lane — never committed): bare-ASGI pass-through proxy on 127.0.0.1:4124. Inbound OpenAI-compatible (daemon lanes unchanged: base_url localhost:4124/v1, any/dummy key accepted — proxy REPLACES Authorization outbound). Outbound https://llm.ensem.dev/v1 with the real key. Full-body inbound; streamed outbound (SSE chunk passthrough for streaming completions — verified). Logs: method/path/upstream-status/latency ONLY. POSTGRES_*/PG* scrubbed at process start (belt-and-braces; proxy touches no DB). Path quirk handled: strips leading /v1 (upstream base already carries it).
- Runs as detached service `p3-llm-gateway` (pid 1979093; log /home/nea/agents-ensemble/data/services/p3-llm-gateway.log) — survives daemon restarts; started via the sanctioned long-lived service lane (user-requested standing gateway).
- Two implementation iterations before green: (1) Starlette default-route misuse (TypeError) → rewritten bare ASGI; (2) /v1 double-prefix → upstream 404 → prefix strip. Both documented in service log history.

## 4. Both-half verification probes (via :4124, dummy inbound key)
- **Direct backend pre-probe** (fail-fast before building): GET /v1/models → 200 (12 real models: agentic, agentic-mini, agentic-turbo, coding, coding2, deepseek, glm-4.5-air, grok-latest, minimax-fast, quick, ultimate-ha, ultimate-kha; NOTE: "vision" NOT listed but ROUTES — alias handled server-side). TEXT direct (agentic, max_tokens 80) → 200, real generation: "The capital of France is Paris, and 6 × 7 = 42." (backing model glm-5.3, real reasoning_content present). VISION direct (vision + 32x32 solid-red PNG) → 200, real perception: "deep red/crimson" (backing model MiniMax-M3).
- **Through :4124**: models → 200 (list above). TEXT (agentic, max_tokens 150) → 200 in 1.6s, real echo-free generation: "Saturn is a planet with rings, and 9 squared equals 81." (finish=stop). NOTE: low max_tokens can yield empty content with finish=length — glm-5.3 spends budget on reasoning_content first; real-model behavior, not a proxy defect. VISION (vision + same PNG, "what single color") → 200, real perception: "a solid color swatch … deep, rich red - almost crimson or burgundy". STREAMING (quick, stream=true) → SSE passthrough verified (reasoning_content delta chunks from glm-5.3-flash). Gateway log lines carry zero key/body material (verified).
- **Echo-free**: outputs differ from inputs; perception answers describe the actual test image; generations answer the actual questions.

## 5. Known real-backend behaviors (awareness for proof runs)
- The vision-path backing model (MiniMax-M3) emits reasoning INLINE in content (`<think>...</think>`) rather than in reasoning_content — agents receiving vision turns may see <think> text in message content. Prompt-level handling; not a proxy concern.
- reasoning-bearing text models (glm-5.3 family) + the ensemble reasoning-echo contract: captured reasoning_content echoes per contract; denylist env (OPENAI_REASONING_ECHO_DISABLED_MODELS) available if a backend rejects it.

## 6. Disposition follow-through
- :8081 p3 daemon stays UP (launchpad for proof (b)); no POSTGRES_* exposure (proxy scrubs; daemon booted scrubbed); nothing merged/pushed/cleaned; evidence file is the only repo change.
- Proof (b) (live agent-turn §5 runbook) holds for caller GO; P2 proof (a) re-dispatch is the caller's lane once this file confirms aliveness.
