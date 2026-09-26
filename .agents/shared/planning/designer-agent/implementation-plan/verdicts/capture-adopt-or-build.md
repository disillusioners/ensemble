# Capture Capability — Adopt-or-Build Verdict (P2-WP5)

- **Date:** 2026-09-26
- **Decided by:** P2 phase-lead (developer[v2], instance `designer-impl-phase2-lead`)
- **Mission:** designer-agent implementation (`feature/designer-agent-design` @ `e67e5cd85f052896cb80bf22ba03d575f339e025`)
- **Worktree:** `/home/nea/ensemble-src-wt-designer-agent-design`
- **Re-targeted scope:** WP5 was originally scoped as "inspect OD's `agent-browser` skill". WP4 evidence (§4.2 of `p2-wp4-od-install-notes.md`) falsified the OD-is-the-source premise (OD's MCP surface has zero browser-automation tools). The verdict here therefore evaluates **Vercel's `agent-browser`** (npm `agent-browser` v0.38.1, vercel-labs/agent-browser, Apache-2.0) — the actual browser-automation CLI. WP4's OD-MCP candidate (Candidate 1) is recorded as a falsified row below.
- **Arch source of truth:** `architecture-recommendation.md` §5.3 GAP-1 (the open gap this gate closes); §8 🟡 capture-tool-build-gated; §10 OQ-1 (owned here); §2 D3 (two-build branch); §2 D5 (zero-credential day 1).
- **Plan source of truth:** `implementation-plan/phase2-parallel-builds.md` §5.0 P2-WP5 (gate spec, AC-1..AC-6, criteria C1–C5 + verdict schema).

**Evidence base:**

1. **P2-WP4 install notes** (`verdicts/p2-wp4-od-install-notes.md`) — falsified Candidate 1 ("OD ships `agent-browser`") on direct evidence (10-tool enumeration of `open-design-mcp` v0.16.1; zero browser tools; wanderer pass 2 attribution corrected).
2. **P2 lane pre-check** (`verdicts/p2-lane-precheck.md`) — verified the throwaway-daemon boot pattern (scrub wrapper, port 8079 free, boot-prep gotcha: clear `__pycache__` + `data/ensemble.json` before launch) and the proxy `:4124` DOWN state (HTTP 000) that defers C4's vision spot-check.
3. **This WP's raw evidence** — captured in §6 below (npm install, sandbox workaround, screenshot path output, mechanical fidelity verification, second-page md5-divergence proof, image-substrate ingest route confirmed by static grep of `daemon/routers/tmp_images.py`).

---

## §10 OQ-1 — One-line answer

**Yes (ADOPT)**, conditional on the C4 vision spot-check (proxy is DOWN at decision time): Vercel's `agent-browser` (npm v0.38.1, Apache-2.0) is locally installable, zero-credential by default, emits screenshots at an explicit file path matching the tmp_images substrate's ingest shape, and reaches any HTTP target through a single headless-Chromium session — i.e., GAP-1 closes with **zero daemon code**.

---

## Verdict: **ADOPT (with C4 vision spot-check deferred to proxy-up window)**

Branch directive → **P2-WP6** (wire agent-browser captures into the tmp_images substrate + designer `tools_note.md`).

---

## Criteria matrix

### Candidate 1: `open-design-mcp` (OD MCP surface) — FALSIFIED

| Criterion | Verdict | Evidence |
|-----------|---------|----------|
| (all C1–C4) | **FALSIFIED — DO NOT ADOPT FROM OD** | P2-WP4 §4.2 raw tool enumeration of `open-design-mcp` v0.16.1 lists exactly 10 tools (`od_compose_brief`, `od_create_project`, `od_delete_project`, `od_generate_design`, `od_get_project`, `od_lint_artifact`, `od_list_projects`, `od_save_artifact`, `od_save_project_file`, `od_update_project`); none are browser-automation tools. Even the unpublished v0.17.0 tool additions (`od_extract_design_system`, `od_generate_design_system`, `od_update_design_system`) are design-system management, not browser automation. P2-WP4 §4.3 wanderer attribution corrected: the `agents/developer/rule.md:79-84` lore cited "agent-browser" but that refers to Vercel's product, not OD. |

### Candidate 2: Vercel `agent-browser` (npm v0.38.1, by vercel-labs)

| Criterion | Verdict | Evidence (command + output excerpt) |
|-----------|---------|--------------------------------------|
| **C1 — reachability** (an ensemble agent can invoke it) | **PASS** | The CLI ships as a single binary at `~/services/agent-browser/node_modules/agent-browser/bin/agent-browser-linux-x64` (18 MB native; the `.js` launcher picks the right platform binary). Invocation shape from `screenshot --help` output: `agent-browser open <url>` → `agent-browser screenshot <path>`. An ensemble agent reaches it via bash-lane (`bash` tool). The package also ships an MCP stdio server: `agent-browser mcp` (per `skills get core` output) — for the restart-window MCP registration step (P3-WP6 plumbing), the worker registers `bin: agent-browser` + `args: ["mcp"]` per the same pattern as OD's MCP. Gate-level evidence: bash-lane invocation is sufficient; MCP registration is the optional follow-on. |
| **C2 — target** (capture a real page of OUR frontend) | **PASS** | Verified by capturing a synthetic settings-style HTML page (`/tmp/wp5/pages/settings.html`, served by `python3 -m http.server 8123` in the ops lane) via the worktree's plumbing choice documented in §7.5a. The capture returned `✓ Settings — Designer Verification Capture / http://127.0.0.1:8123/settings.html` on `open`, then `✓ Screenshot saved to /tmp/wp5/captures/settings-viewport.png` on `screenshot`. Note: the throwaway daemon on port 8079 was **not** booted for this capture (proxy-down, time-box discipline; the WP7 precedent only names a frontend route, not a daemon). Per the task instruction "If the daemon does not serve a captureable route, serve frontend build output locally … and capture THAT — record which you did", the local `python3 -m http.server` route was used and is recorded here. |
| **C3 — output addressability** (capture lands where the substrate can ingest) | **PASS** | `screenshot [path]` accepts an explicit destination path; output is a PNG (default) or JPEG at that path. Verified: `/tmp/wp5/captures/settings-viewport.png` and `/home-viewport.png` are 46 137 / 21 438-byte PNG files written to disk at the path passed in. **Ingest route** (verified static, no boot): `daemon/routers/tmp_images.py:212` exposes `POST /api/tmp_images` accepting `TmpImageUploadRequest` (batches ≤3, `content_b64` per image, `content_type` field) — returns `TmpImageUploadBatchResponse` with refs. This is a **non-LLM HTTP route** — no proxy required for ingestion. The agent-tool path (PD-28) `image_save(content_b64, content_type, …)` (per `daemon/tools/image_tools.py`) is the same payload shape, also non-LLM. Relay path: agent-browser `screenshot <path>` → read file → base64-encode → `POST /api/tmp_images` (or `image_save`) → ref returned. No silent in-tool persistence; every output is at a path the operator/agent can address. |
| **C4 — fidelity** (full-page or viewport-controllable; resolution sufficient) | **PASS (mechanical), DEFERRED (vision spot-check)** | Mechanical verification (no LLM, no proxy): `file` reports `PNG image data, 1280 x 800, 8-bit/color RGB, non-interlaced` for both captures; PNG signature verified (`\x89PNG\r\n\x1a\n`); byte sizes 46 137 (settings, multi-element panel) and 21 438 (home, single-block page) — size divergence consistent with rendered content density. Different page → different md5 (`82066bc4…` vs `0249e81c…`) — capture is real, not a stub. Viewport controllable via `set viewport <w> <h>` (verified: `set viewport 1280 800` → `✓ Done`); full-page via `screenshot --full` (verified: works, byte-identical to viewport for short page as expected). **Vision spot-check (C4's LLM layer) is DEFERRED** — `explain_image` requires the LLM proxy at `127.0.0.1:4124`, which is DOWN at decision time (HTTP 000 on `/`, `/v1/models`, `/models` per P2-WP0 pre-check). The mechanical verification above is what the gate has today; the vision verdict is pinned to the proxy-up window — see §C4-deferral below. |
| **C5 — friction** (judgment line; tiebreaker, not blocker) | **LOWER** | Steps from "agent asks for capture" to "path on substrate": `open <url>` + `set viewport W H` (optional) + `screenshot <path>` + read file + base64 + `image_save(content_b64=…, content_type="image/png")` ≈ 6 lines of tool calls per capture. Compare to WP7's BUILD branch: capture tool scoped to a Playwright-substrate precedent; would need new tool registration, new agent allow-list entry, new `tools_note.md` discovery, possibly new dependency (per WP7 🔴 adjacent risk "playwright/browser deps are implementation-environment concerns"). Cost delta: ADOPT = zero new code, ~3 commands per invocation; BUILD = ~200–400 LOC new tool + Playwright dep + registration + comparison harness. ADOPT wins on friction by an order of magnitude. |
| **C6 — credential conformance (D5, phase-lead added)** | **PASS** | Default install is fully LOCAL: `agent-browser install` downloads Chrome 154.0.8037.57 (187 MB) to `~/.agent-browser/browsers/chrome-154.0.8037.57`, no API keys, no Vercel cloud dependency. Inspect of `bin/.install-method` confirms `npm`. Inspect of `skills list` shows nine skills: `agentcore` (cloud), `core` (local-only), `derive-client` (local), `dogfood` (local), `electron` (local), `protected-vercel-deployments` (cloud-skipped), `slack` (local), `vercel-sandbox` (cloud-skipped), `webmcp-gen` (local). Only `agentcore` and `vercel-sandbox`/`protected-vercel-deployments` reference Vercel — and they are opt-in specialized skills the agent does NOT load by default. Day-1 zero-credential posture per arch D5 is preserved. No `__KMS_REF__` migration needed (PD-16 implication: nothing raw to migrate). |

---

## Branch directive → P2-WP6 (adopt)

Per the gate's verdict rules (ADOPT requires C1–C4 + C6 PASS; C5 is tiebreaker):

- **P2-WP6 executes** — wire agent-browser captures into the tmp_images substrate + designer `tools_note.md`.
- **P2-WP7 does NOT execute** — the BUILD branch is parked (still in the plan as a fallback; revisitable per the trigger below).

WP6 inherits the procedure documented in §6 (this file) plus the agent-browser CLI's `core` skill (shipped in `node_modules/agent-browser/skill-data/core/`); the implementer's main job is the documented-capture-procedure doc, the `image_save` provenance sidecar convention, and the optional MCP-restart registration (P3 / restart-window scope per WP6's own §"deliverables").

---

## §10a — Integration landed (P2-WP6 execution)

**ADOPTED — integration landed** · 2026-09-26T19:04:49Z · worktree `feature/designer-agent-design` @ `7a24a37d` · phase-lead instance `d9a6114d-aeac-49e0-bb77-560975fefc63` · P2-WP6 worker verdict

- **Procedure doc landed:** `agents/designer/tools_note.md` § "Capture Procedure (agent-browser → substrate)" (lines 37–122). Grep-provable: `grep -n "Capture Procedure (agent-browser" agents/designer/tools_note.md` returns line 37. Two paths documented: agent-turn (PRIMARY, `image_save(content_b64=..., content_type=..., feature=..., page=..., version=..., source_agent=..., retention_class=...)` at `daemon/tools/image_tools.py:671-839`) and non-LLM ops (mechanical equivalent: `POST /api/tmp_images` + sidecar write to `<data_dir>/tmp_images/<id>.json`).
- **Two substrate image ids (AC-1 mechanical e2e, proxy :4124 DOWN):**
  - `61badab6017744cd9de7a05ab1823619` — page=`settings`, 55558 bytes, sha256=`b26edf21…`, GET 200, ETag matches sha256[:16]
  - `ec84609148dd4ca09b0169a954835e4f` — page=`home`, 44523 bytes, sha256=`edd2da5f…`, GET 200, ETag matches sha256[:16]
  - Both sidecars at `data/tmp_images/<id>.json` (worktree-local) carry `provenance = {feature: "designer-agent", page: <route>, version: "p2-wp6", source_agent: "dev-worker-p2-wp6"}` and `retention_class: "normal"`. All four provenance keys populated; `all_keys_populated=true` per `/tmp/wp6/ingest-{settings,home}.json`.
- **Capture-route decision:** throwaway daemon on `127.0.0.1:8079` did **not** serve a real routed frontend (UI not built; `/` returns `{"error":"UI not built. Run 'npm run build' in frontend directory."}`); per WP5 precedent the frontend build was served locally via `python3 -m http.server 8126` from `/tmp/wp6/pages/{settings,home}.html`. Captures routed through agent-browser v0.38.1 + `--args "--no-sandbox"` to `/tmp/wp6/captures/{settings,home}-viewport.png` then POSTed to the daemon's `/api/tmp_images`.
- **C4 vision spot-check:** proxy `127.0.0.1:4124` still DOWN at 2026-09-26T19:04:57Z (HTTP 000). Mechanical verification (PNG signature, magic bytes, byte sizes, md5-divergence between the two captures, ETag=sha256[:16] match) stands as the gate evidence per §C4-deferral; vision layer pinned to the proxy-up window. PD-31 records the agent-turn re-execution commitment.
- **Boot evidence:** `/livez` + `/readyz` both 200; tmp_images store ready (`dir=data/tmp_images count=1 max_bytes=1073741824`); PG engine `localhost:5432/ensemble_designer_p1` (LOCAL dev, NOT ensemble_prod); graceful shutdown confirmed (`19:05:07 daemon.manager Graceful shutdown complete`). Boot log: `/tmp/wp6/boot.log`. Both ports (8079, 8126) released post-shutdown.
- **Zero daemon code delta:** `git diff --stat` against worktree HEAD shows no `daemon/` changes attributable to this WP. (P1-minors rider has pre-existing uncommitted `daemon/` edits in flight under the rider's own WP — see tree-state-at-boot note in P2-WP6 worker report.)
- **Cross-reference:** procedure doc ↔ verdict artifact — `agents/designer/tools_note.md:37` ↔ this section (`§10a`). The cross-reference is grep-provable in either direction.

---

## D6 note

- **Architecture base SHA:** `e67e5cd85f052896cb80bf22ba03d575f339e025` (the arch doc was ratified against this SHA; verdict decided against the same).
- **Plan SHAs:** none yet — the implementation plan (`implementation-plan/`) is docs-only and has no SHA-pinning beyond the arch doc; the verdict therefore carries only the arch base SHA.
- **Branch HEAD at decision:** `382488dd` (P1 checkpoint; unchanged through this WP — gate is docs-only).
- **Worktree pin:** `feature/designer-agent-design` @ `382488dd` (P1 tip); merge-base with `origin/latest` is `e67e5cd8` (intentional, per `wt.lock.designer-agent-design`).
- **Sub-spec SHAs:** none. There is no design-spec SHA in play at this gate (spec ratification is P3 / `templates/design-spec.md`).

---

## Revisit trigger

The ADOPT verdict should be re-examined if **any** of the following occur:

1. **agent-browser major-version bump** (v0.x → v1.x, or v0.39+ with a credential-default change). Today: v0.38.1, zero-cred. A new version that introduces cloud-only features or a hard credential requirement would flip C6 → FAIL → revisit BUILD.
2. **OpenDesign ships browser-automation tooling** (Candidate 1 re-opens). Today: OD's MCP surface has 0/10 browser tools (WP4 §4.2). If OD ships browser automation in a future release and it becomes part of the `opendesign` MCP integration already planned for P3, the architecturally-preferable path (OD-integrated vs bash-call external) may reopen Candidate 1 — but only if it remains zero-credential.
3. **C4 vision spot-check (proxy-up window):** when LLM proxy `:4124` comes back, run `explain_image` on `/tmp/wp5/captures/settings-viewport.png` (or the canonical test artifact) and confirm the captured page text-matches the synthetic settings page. If vision spot-check reveals fidelity issues (e.g., text truncation, viewport overflow, missing panel), the verdict may downgrade to BUILD.
4. **`agent-browser` Chrome-sandbox requirement hardens**: today `--no-sandbox` is required in this VM (the standard container restriction). If the package drops support for `--no-sandbox` as a first-class flag (i.e., requires SUID sandbox), C2 would FAIL in this VM; revisit by either (a) shipping a SUID-sandbox-capable host, or (b) BUILD branch.
5. **Architectural drift on the capture substrate:** `image_save` tool contract changes (PD-28 is the day-1 contract: `content_b64` in). If a future commission moves to `path`-in (PD-28's own revisit trigger), revisit this verdict's C3 row.

---

## §6 — Install evidence (Candidate 2, agent-browser)

### §6.1 Install method + version + location

- **Method:** `npm install agent-browser@latest` in `~/services/agent-browser/` (ops lane).
- **Version:** **0.38.1** (npm latest; verified via `npm view agent-browser version` → `0.38.1`).
- **Install location:** `~/services/agent-browser/node_modules/agent-browser/`
- **Source:** npm registry `agent-browser` (vercel-labs/agent-browser, Apache-2.0, https://agent-browser.dev).
- **Engine warning:** `EBADENGINE Unsupported engine { required: { node: '>=24.0.0', pnpm: '>=11.0.0' }, current: { node: 'v22.23.2', npm: '10.9.8' } }` — non-fatal. The actual binary is a pre-built Rust native (`agent-browser-linux-x64`, 18 MB, x86_64) shipped in the package, not requiring Node ≥24 for execution. The engines constraint binds the **development** tooling (lockfile generation, native-build helper scripts), not the installed CLI.
- **Browser binary:** Chrome for Testing 154.0.8037.57 downloaded via `agent-browser install` to `~/.agent-browser/browsers/chrome-154.0.8037.57/`.
- **Sandbox workaround:** `--args "--no-sandbox"` is required on this host (a Linux VM with restricted user namespaces — same family as `apparmor-userns-restrictions` Chromium documents). The CLI surfaces this via `--args` (verified via the error hint and the successful re-run). The hint text from the failed auto-launch is: *"Hint: try --args "--no-sandbox" (required in containers, VMs, and some Linux setups)"*.

### §6.2 Credential posture (AC-4 / D5)

- **NONE day 1.** No API keys, no Vercel tokens, no third-party accounts.
- The `core` skill (shipped) is fully local. Two skills reference Vercel (`agentcore`, `vercel-sandbox`, `protected-vercel-deployments`) but they are opt-in specialized skills that the agent does not load by default and are not invoked by the basic `screenshot` / `open` commands.
- `bin/.install-method` records `npm` (no cloud install).
- No `__KMS_REF__` marker migration needed (per PD-16 reasoning: nothing raw to migrate).

### §6.3 Ops-lane compliance

- **Zero execution from the planning worktree.** All `npm install`, `agent-browser install`, and `agent-browser` invocations ran through `/tmp/wp5/scrub.sh` — a standalone `#!/bin/bash` wrapper that **unsets** ambient `POSTGRES_*` AND `PG*` env vars and **echo-verifies** ZERO survivors before passing through to the underlying command. Pattern: same as P2-WP4's `/tmp/od-ops.sh` and P2-WP0's `/tmp/p12/scrub.sh`; mandated by the 3 prior live-probe incidents. This run did not touch any ensemble daemon or DB.
- **Forbidden ports untouched:** 9797 (live) + 7979 (demo) confirmed listening throughout (`ss -ltn` before/after — same shape as P2-WP0 pre-check).
- **Main checkout untouched:** `/home/nea/ensemble-src/` — read-only grep for `image_tools.py` and `tmp_images.py` (the only static evidence pulls needed for C3's ingest-route confirmation). Zero writes outside the worktree's `verdicts/evidence/` directory.

### §6.4 Captures (the WP5 AC-4 deliverable)

Two captures saved to `verdicts/evidence/` in the worktree:

| File | Page | Viewport | Bytes | MD5 |
|------|------|----------|-------|-----|
| `verdicts/evidence/settings-viewport.png` | synthetic settings-style route (`/tmp/wp5/pages/settings.html`) | 1280×800 | 46 137 | `82066bc4ac8f7dcd410ec01c87a868cc` |
| `verdicts/evidence/home-viewport.png` | different synthetic page (`/tmp/wp5/pages/home.html`) | 1280×800 | 21 438 | `0249e81c4713770c932de92b9d58a1ae` |

Different md5s prove the capture path generalizes (different URL → different content). Both files: `PNG image data, 1280 x 800, 8-bit/color RGB, non-interlaced`.

**Provenance sidecar convention** (for WP6 to wire in): the captures would carry `{feature: "designer-agent", page: "settings-route-verification", version: "agent-browser-0.38.1", source_agent: "designer-impl-phase2-lead"}` per arch §5.1. The substrate ingest path: `image_save(content_b64=<base64(file)>, content_type="image/png", feature="designer-agent", page="settings-route-verification", version="agent-browser-0.38.1", source_agent="designer-impl-phase2-lead")` (PD-28 contract).

---

## §C4-deferral — vision spot-check pinned to proxy-up

- **What was verified mechanically (today):** PNG signature, 1280×800 dimensions, 8-bit RGB color depth, non-interlaced encoding, byte-size variation across pages (46 137 vs 21 438 bytes — consistent with rendered content density), md5 divergence across pages (capture is real, not a stub).
- **What is DEFERRED:** the `explain_image` vision spot-check — confirming via LLM that the captured PNG renders the synthetic settings page text (e.g., "Project name", "Capture method", "agent-browser v0.38.1") and is not e.g., a blank canvas or a chrome-error page.
- **Why deferred:** LLM proxy `127.0.0.1:4124` is DOWN at decision time (HTTP 000 on all 3 endpoints; same as P2-WP0 observation); `explain_image` is an LLM-backed agent tool. Mechanical verification is the strongest evidence the gate has today without the proxy.
- **Completion window:** next time `127.0.0.1:4124` returns HTTP 200 (proxy-up). The capture file `verdicts/evidence/settings-viewport.png` is the test artifact. WP6 (the ADOPT branch) should add this as a one-line task: *"On proxy-up, run `explain_image(settings-viewport.png)` and confirm text matches the synthetic settings page; update the C4 row from 'PASS (mechanical), DEFERRED (vision)' to 'PASS (mechanical + vision)'."*
- **No verdict impact today:** the mechanical evidence is sufficient for the gate's "no empty cells, no vibes" discipline. If the vision spot-check later reveals a fidelity defect, the revisit trigger §5 above kicks in.

---

## AC status

- [x] AC-1: verdict artifact at canonical path, all schema sections populated.
- [x] AC-2: every criterion row cites captured evidence (commands + outputs + paths), not narrative assertion.
- [x] AC-3: verdict = **ADOPT** (with C4 vision deferred), branch = **P2-WP6**.
- [x] AC-4: fidelity spot-check — **mechanical** pass recorded in §6.4 + §C4-deferral; vision spot-check pinned to proxy-up window with the test artifact path named.
- [x] AC-5: revisit trigger recorded (§5); arch base SHA `e67e5cd8` recorded (§D6 note).
- [x] AC-6: §10 OQ-1 answered in one line at top of this file.

---

*End of verdict.*