# P2-WP4 — Early self-hosted OpenDesign install (manual/ops lane) — Verdict

**Status: PARTIAL — MCP server installed; OD daemon install BLOCKED on host (O-3 triggered)**

- **Date:** 2026-09-26
- **Author:** developer[v2] / P2 phase-lead (instance `designer-impl-phase2-lead`)
- **Mission:** designer-agent implementation (`feature/designer-agent-design` @ `e67e5cd8`, pinned to base)
- **Worktree:** `/home/nea/ensemble-src-wt-designer-agent-design`
- **WP source of truth:** `implementation-plan/phase2-parallel-builds.md` §5.0 P2-WP4 + AC-1..AC-6
- **Arch source of truth:** `architecture-recommendation.md` §5.2 / §5.3 GAP-1 / §7.1–7.3
- **PD source of truth:** `implementation-plan/decisions.md` PD-8 (dependency inversion) + PD-14 (O-3 ops-lane)

---

## 1. TL;DR

| AC | Verdict | Notes |
|----|---------|-------|
| AC-1 OD instance reachable on host | **BLOCKED** | Daemon cannot run on this Linux VM — see §6 |
| AC-2 `opendesign` MCP visible to daemon (post-restart) | **DEFERRED** | Per spec, MCP registration is the P2 restart window's job; values recorded here for that window (see §5). Daemon-not-running prevents the live registration verification. |
| AC-3 `agent-browser` skill presence in OD skill surface | **ANSWERED — NO** | See §4. The MCP tool list (the canonical "skill surface" for the MCP integration) contains zero browser-automation tools. WP5's adopt-or-build verdict is therefore **NOT "adopt from OD"** — the arch doc's premise that "agent-browser = OD-shipped skill" appears wrong against the actual evidence. |
| AC-4 credential posture recorded | **MET** | None — no keys day 1. See §5.3 |
| AC-5 dependency-inversion note present | **MET** | See §7 |
| AC-6 install performed from ops lane, zero execution from planning worktree | **MET** | See §8 |

The MCP server (the part that doesn't need the daemon to be useful for inspection) IS installed and its tool surface is enumerated. The daemon itself — which is what hosts the `127.0.0.1:7456` HTTP API the MCP calls into — cannot run here.

---

## 2. Install method + version + host shape

### 2.1 What is "OpenDesign" (verified)

- **Canonical repo:** `github.com/nexu-io/open-design` (Apache-2.0; latest release `open-design-v0.24.1` 2026-09-24)
- **Canonical MCP server:** npm `open-design-mcp` (by `nano-step001`; bridges coding agents to OD daemon)
- **MCP source repo:** `github.com/nano-step/open-design-mcp`
- **Companion CLI:** npm `open-design-axi` (AXI-compliant CLI companion by `brycehamrick`)
- **Not** to be confused with: npm `opendesign` (avocode's `.octopus` design-file WASM CLI; last release 2023-05-22) — different product entirely

### 2.2 Install method (per OD docs)

The official Linux install path is **Docker**: `deploy/docker-compose.yml` + `deploy/docker-compose.linux.yml` from `nexu-io/open-design`. Documented commands (per `QUICKSTART.md`):

```bash
cd deploy
cp .env.example .env
# edit .env, paste OD_API_TOKEN=$(openssl rand -hex 32)
docker compose up -d
# open http://127.0.0.1:7456
```

Daemon image: `ghcr.io/nexu-io/od:latest` (built from `deploy/Dockerfile`).

A source-build path also exists (`pnpm tools-dev` is the only sanctioned local lifecycle entry per `AGENTS.md`) but requires:
- Node `~24` (engines field in `package.json`)
- `pnpm@10.33.2` (Corepack-pinned)
- `better-sqlite3` native compile (`node-gyp`) on non-standard targets

No pre-built Linux binary has ever been published across all checked releases (v0.22.2, v0.23.0, v0.24.0, v0.24.1) — only `.dmg` (macOS ARM+x64) and `-win-x64-setup.exe`.

### 2.3 Host shape

```
Linux ensemble-vm 6.8.0-139-generic GNU/Linux x86_64
Node:    v22.23.2     (vs required ~24)
npm:     10.9.8
npx:     10.9.8
Docker:  NOT INSTALLED        ← hard blocker for official Linux path
Podman:  NOT INSTALLED
buildah: NOT INSTALLED
nerdctl: NOT INSTALLED
corepack: /usr/bin/corepack   (EACCES on symlink to /usr/bin/pnpm — non-blocking for node-only installs)
nvm / fnm / volta: NONE
pnpm:    not pre-installed (corepack couldn't enable globally due to EACCES)
```

Network egress: `registry.npmjs.org` and `github.com` both reachable from host.

---

## 3. What I actually installed (best-effort, in scope)

### 3.1 `open-design-mcp` v0.16.1 (npm latest) — INSTALLED

Install location: `~/services/opendesign/` (ops lane, OUTSIDE planning worktree).

```
/tmp/od-ops.sh npm init -y   # creates package.json
/tmp/od-ops.sh npm install open-design-mcp@latest
# → added 96 packages, audited 97 packages in 5s
# → 0 vulnerabilities
```

Installed structure:

```
~/services/opendesign/
├── package.json
├── package-lock.json
└── node_modules/
    └── open-design-mcp/
        ├── package.json  (version 0.16.1)
        ├── README.md
        ├── LICENSE  (Apache-2.0)
        ├── NOTICE
        ├── dist/
        │   ├── src/
        │   │   ├── server.js       (entry point — MCP stdio)
        │   │   ├── config.js       (env validation; OD_DAEMON_URL required)
        │   │   ├── od-client.js
        │   │   ├── sse-parser.js
        │   │   ├── types/metadata-stash.js
        │   │   └── tools/
        │   │       ├── compose-brief.js       (od_compose_brief)
        │   │       ├── create-project.js      (od_create_project)
        │   │       ├── delete-project.js      (od_delete_project)
        │   │       ├── errors.js              (shared types, NOT a tool)
        │   │       ├── generate-design.js     (od_generate_design)
        │   │       ├── get-project.js         (od_get_project)
        │   │       ├── index.js               (tool registry, NOT a tool)
        │   │       ├── lint-artifact.js       (od_lint_artifact)
        │   │       ├── list-projects.js       (od_list_projects)
        │   │       ├── save-artifact.js       (od_save_artifact)
        │   │       ├── save-project-file.js   (od_save_project_file)
        │   │       └── update-project.js      (od_update_project)
        │   └── vendor/od-contracts/   (vendored OD API contracts)
```

### 3.2 Service state

The MCP server was invoked once for a smoke-test:

```
$ /tmp/od-ops.sh ./node_modules/.bin/open-design-mcp --help
[open-design-mcp] FATAL: invalid core env vars
  - OD_DAEMON_URL: Required
Required: OD_DAEMON_URL (valid URL). Optional: OD_API_TOKEN, OD_AUTH_MODE, OD_BASIC_USER, OD_BASIC_PASS.
```

This is the **correct behaviour** for an MCP stdio server when its backing daemon is absent — the server validates env at startup, refuses to start without `OD_DAEMON_URL`, and would never expose tools if it couldn't reach the daemon. Verified `OD_DAEMON_URL` is the only hard requirement (per `config.js`).

**Verdict:** MCP server = INSTALLED but NOT RUNNING (intentionally — no daemon). The OD daemon = NOT RUNNING (BLOCKED, see §6).

### 3.3 Other install attempts that did NOT happen (deliberately)

- `docker compose up -d` — NOT attempted: no Docker on host
- `pnpm tools-dev` (source-build path) — NOT attempted: Node 22 vs required ~24, plus corepack EACCES on global symlink
- Full repo clone of `nexu-io/open-design` — NOT attempted: would consume most of the 45-min timebox just for the clone + pnpm install of a full pnpm workspace monorepo

---

## 4. agent-browser skill presence — RAW EVIDENCE

### 4.1 Answer

**NO agent-browser skill in `open-design-mcp` v0.16.1's MCP tool surface.** The MCP server exposes exactly **10 tools** (verified via `dist/src/tools/*.js`); none are browser-automation tools.

### 4.2 Raw tool surface excerpt (the WP5 gate input)

```
$ ls ~/services/opendesign/node_modules/open-design-mcp/dist/src/tools/*.js | grep -v 'errors.js\|index.js'
compose-brief.js       → od_compose_brief       "Format a Turn 3 prompt for od_generate_design.
                                                  Combines Turn 1 form answers, brand spec, and page
                                                  brief into a string upstream Open Design recognizes.
                                                  Pure function — no network, no env vars."
create-project.js      → od_create_project      "Create a new project on the Open Design daemon.
                                                  Returns the project details and an auto-seeded
                                                  conversation ID. Requires only OD_DAEMON_URL."
delete-project.js      → od_delete_project      "PERMANENTLY delete a project. The Open Design
                                                  daemon removes the database row AND the on-disk
                                                  project directory. This cannot be undone.
                                                  Requires only OD_DAEMON_URL."
generate-design.js     → od_generate_design     "Generate a design artifact using BYOK. Composes
                                                  the upstream Open Design system prompt and
                                                  proxies through OD's /api/proxy/<provider>/stream
                                                  endpoint. Requires BYOK_BASE_URL..."
get-project.js         → od_get_project         "Fetch a project + its artifact files. Read-only;
                                                  requires only OD_DAEMON_URL. Output includes
                                                  customInstructions if set on the project."
lint-artifact.js       → od_lint_artifact       "Validate an HTML artifact for structural issues.
                                                  Returns text findings + optional agent message."
list-projects.js       → od_list_projects       "List all projects from the configured Open Design
                                                  daemon. Read-only; requires only OD_DAEMON_URL."
save-artifact.js       → od_save_artifact       "Persist an HTML artifact to the daemon under a
                                                  slug identifier. Requires only OD_DAEMON_URL."
save-project-file.js   → od_save_project_file   "Persist a file (typically HTML from od_generate_design)
                                                  INSIDE a project so it appears in
                                                  od_get_project.files[] and renders in the daemon UI."
update-project.js      → od_update_project      "Update a project on the Open Design daemon. At
                                                  least one mutable field (name, customInstructions,
                                                  metadata) is required. Requires only OD_DAEMON_URL."
```

Total MCP tool count (v0.16.1): **10** (excluding `errors.js` shared types and `index.js` tool-registry).

Note on tool-list drift: the `open-design-mcp` README (master branch) describes a v0.17.0 release with 13 tools, adding `od_extract_design_system`, `od_generate_design_system`, `od_update_design_system`. **Even with v0.17.0, none of the new tools are browser-automation tools.** They are design-system generation/management tools.

### 4.3 — `agent-browser` cross-reference (negative result)

The arch doc's evidence-index line 402 records: *"Wanderer pass 2 ... `agent-browser` = OD-shipped skill."* This premise was the basis for GAP-1's "verify OD `agent-browser` skill adoption first — may close the gap for free" (arch §5.3, §8 🟡).

**This premise does NOT match the installed evidence:**

- `open-design-mcp` v0.16.1 tool list contains no browser/automation/screenshot tool
- `open-design-axi` README describes OD as a "filesystem of functional skills, rendering design templates, design systems, and plugins" (no browser automation)
- The `agents/developer/rule.md:79-84` lore that the wanderer inferred from ("Recommend agent-browser ONLY for web frontend projects — Do browser automation (use agent-browser skill)") more plausibly refers to **Vercel's `agent-browser`** (npm `agent-browser` v0.38.1, Apache-2.0, by Vercel/vercel-labs) — a separate, standalone CLI for browser automation that Vercel positions for use by "Claude Code, Cursor, GitHub Copilot, OpenAI Codex, Google Gemini, opencode, and any agent that can run shell commands"
- The two products are *co-installable* but distinct: Vercel's `agent-browser` is the browser-automation CLI; OD is the design-canvas daemon

### 4.4 Implication for WP5 (adopt-or-build verdict)

WP5's verdict **cannot** be "adopt agent-browser from OD". The candidate must be reframed:

- **Option A — ADOPT (Vercel agent-browser):** Install Vercel's `agent-browser` npm package as a SEPARATE piece of tooling, wire it via shell-call from a worker (not via MCP, since it has no MCP server of its own). Pros: zero daemon work, real product, Apache-2.0, screenshots/DOM/clicks/navigation all there. Cons: not on the OD MCP seam — designer would call it via `bash` not via `mcp_opendesign_*` tools. This means **the ensemble's `opendesign` MCP doesn't deliver capture**; capture becomes a separate `bash`-wrought capability the worker spawns.

- **Option B — BUILD (tester-Playwright precedent):** Build the capture tool as planned (P2-WP7). Pros: stays on the documented architecture path (capture integrated as a first-class worker capability). Cons: more code to ship before WP8.

- **Option C — DEFER to P3 (installer skill scope):** Let the OD MCP install happen in P3 (which has KMS-Lite for credentials + the install-opendesign installer skill), and only THEN re-evaluate whether agent-browser becomes a separate install there. Pros: cleanest handoff to P3. Cons: P2-WP8 E2E rollout needs SOMETHING for capture.

WP5 worker — please pick one of A/B/C and justify. **Do NOT pick "adopt from OD" — that's not on the table.**

---

## 5. MCP registration values-to-use (deferred to restart window)

Per task spec, the actual `mcp_servers` registration is performed by the P2 restart-window daemon (NOT by me — no daemon was up during my run). Here are the values the registration worker should use for the `opendesign` builtin MCP server.

### 5.1 Server-side registration shape (per arch §5.2/§7.1–7.3)

The MCP transport is **stdio** (the daemon spawns the MCP server as a subprocess and pipes JSON-RPC over stdin/stdout — the verified mechanism: `bin: open-design-mcp` → `dist/src/server.js`, declared as `mcpName: io.github.nano-step/open-design-mcp`).

| Field | Value | Source |
|-------|-------|--------|
| Server name (registry key) | `opendesign` | per arch §5.2 / §7.1 (`requires: mcp: [opendesign]`) |
| Transport | `stdio` | verified (`McpStdioConfig` per `daemon/mcp/config.py:190-201`) |
| Command / entry | `open-design-mcp` (resolved at MCP start) — or absolute path `~/services/opendesign/node_modules/.bin/open-design-mcp` if PATH isn't reliable for the daemon's process env | verified (`bin` field in `package.json`) |
| Args | (none — the entry script is the binary) | verified |
| Working dir | `~/services/opendesign` (the ops-lane install root) | install location |
| Env keys (pass-through, KMS-marker) | `OD_DAEMON_URL` (required), `OD_API_TOKEN` (optional; for non-loopback installs only) | verified (`config.js` schema) |
| Required capability | `opendesign` | per arch §7.1 `requires: mcp: [opendesign]` |
| Schema version | `0.16.1` (semver of the npm package — bump when MCP server upgrades) | verified |

### 5.2 Sidecar-end (when OD daemon is finally up) — for the restart worker

The `opendesign` MCP, once registered, will try to connect to:

```
OD_DAEMON_URL=http://127.0.0.1:7456          # OD daemon default port per QUICKSTART
OD_API_TOKEN=<loopback-may-leave-empty>      # per deploy/.env.example
OD_DISABLE_API_AUTH=1                        # for trusted loopback — same behavior as not setting OD_API_TOKEN
```

When the daemon is reachable, `open-design-mcp` will register 10 tools under the `od_*` namespace (see §4.2). Agents with `mcp: [opendesign]` in their skill `requires:` block will then be able to call those tools.

### 5.3 Credential posture (AC-4 — MET)

**NONE — no keys day 1.** Per `deploy/.env.example` and OD docs:

- Loopback installs default to **unauthenticated** (the daemon serves `127.0.0.1:7456` without requiring `OD_API_TOKEN`)
- The documented loopback-only deploy pattern is the **zero-credential** day-1 case the arch doc's D5 ratifies
- If/when a third-party cloud brokering path activates (§7.5a — explicitly deferred), the KMS-Lite mint + `__KMS_REF__` seam becomes operative; nothing day-1 needs that

No credentials configured here. P3's KMS-Lite work is forward-only.

### 5.4 MCP-env-RAW note (carried risk)

The arch doc's 🔴 risk row 366-367 (mcp_servers stores env RAW today; `redact_secrets()` presentation-only) does NOT bite on day 1 here, because:
- `OD_DAEMON_URL=http://127.0.0.1:7456` is not a secret
- `OD_API_TOKEN` is empty for loopback

When P3 adds KMS-Lite mint, the P3 migration WP (PD-16) should sweep the `mcp_servers.config` row for `opendesign` if a token was later added.

---

## 6. BLOCKED-EVIDENCE (O-3)

The OD daemon itself cannot be installed on this host. The blocker is "deps fail irrecoverably" (per O-3 trigger language) — specifically, the host lacks the supported Linux install path's required runtime. Evidence:

### 6.1 Official Linux install path = Docker (BLOCKED)

```bash
$ docker --version
/bin/sh: docker: not found
$ docker compose version
/bin/sh: docker compose: not found
$ which podman nerdctl buildah containerd
(none)
$ ls /var/run/docker.sock /run/docker.sock
(no such file)
```

`deploy/docker-compose.yml` declares:

```yaml
image: ${OPEN_DESIGN_IMAGE:-ghcr.io/nexu-io/od:latest}
```

i.e. the only published artifact for Linux is a Docker image. `deploy/docker-compose.linux.yml` adds `network_mode: host` and host-CLI mounts — also Docker-only.

**No pre-built Linux binary ever shipped.** Verified across v0.22.2, v0.23.0, v0.24.0, v0.24.1 (latest) — assets are only `*-mac-arm64.dmg`, `*-mac-x64.dmg`, `*-win-x64-setup.exe`.

### 6.2 Source-build fallback (BLOCKED — secondary)

OD's `package.json#engines` requires `node: "~24"`. The host has `v22.23.2`.

```bash
$ node --version
v22.23.2
$ corepack enable
Internal Error: EACCES: permission denied, symlink '../lib/node_modules/corepack/dist/pnpm.js' -> '/usr/bin/pnpm'
```

Path forward would need:
1. Install Node 24 (no nvm/fnm/volta on host; would need sudo apt or compile from source)
2. Install pnpm 10.33.2 (without corepack's global symlink, manual install needed)
3. Clone `nexu-io/open-design` (~100s MB; full pnpm workspace)
4. `pnpm install` (will hit `better-sqlite3` native compile)
5. `pnpm tools-dev` (the sanctioned local lifecycle, per `AGENTS.md`)

This sequence would consume the rest of the 45-min budget just for the install, and the daemon's loopback binding + sidecar socket + 128-MiB idle footprint would still need verification. **Out of budget** — that's what the hard time-box is for.

### 6.3 What would unblock

To unblock this WP on a future run, ONE of the following must be true:

1. **Provision a Docker-equipped host** (install `docker.io` + `docker-compose-plugin` via apt) and re-run this WP. Lowest cost.
2. **Provision a macOS or Windows host** (the .dmg/.exe installers just run). Native, official.
3. **Install Node 24 on this Linux host** (apt or compile), then attempt the `pnpm tools-dev` source-build path. Highest cost, highest risk (native compile + sidecar dependencies).

Until one of those is done, the OD daemon stays "not running on this host" and WP5's MCP tool list is the only inspectable artefact.

### 6.4 Single flagged leftover

Per O-3 ruling: this WP's blocker becomes the phase's **SINGLE flagged leftover**. The MCP server is partially shipped (installed, registered-to-be, version-locked). The daemon half is the gap. P3's installer skill work should plan around: (a) the MCP server entry-point lives in `~/services/opendesign/node_modules/.bin/open-design-mcp` already, so the installer's bootstrap step is to verify-and-bump the MCP version rather than re-install from scratch; (b) the daemon install becomes the installer's main job — Docker is the easy path on any docker-equipped host, and the daemon URL is `http://127.0.0.1:7456`.

---

## 7. Dependency inversion note (AC-5 — MET)

PD-8: *"P2-WP4 early self-hosted OD install is MANUAL/OPS-LANE (pre-bootstrap); P3-WP5 install-opendesign skill later GENERALIZES/REPLACES the manual lane."*

What P2-WP4 establishes that P3 generalizes:
- The MCP server install method (`npm install open-design-mcp` in an ops-lane dir) — REUSABLE: P3 installer skill's "install-mcp" substep takes this recipe
- The MCP server env-var contract (`OD_DAEMON_URL` required; `OD_API_TOKEN` loopback-optional) — REUSABLE: P3 captures this in `capabilities.yaml` schema (per arch §7.3)
- The daemon URL default (`http://127.0.0.1:7456`) — REUSABLE: P3 hard-codes as the daemon discovery default
- The tool-list contract (`od_*` namespace, 10 tools in v0.16.1, possibly 13 in v0.17.0) — REUSABLE: P3 enumerates these in the installer's "what tools become available" diagnostic

What P2-WP4 did NOT establish (deferred to P3):
- The Docker setup of the OD daemon itself
- KMS-Lite mint of any credential (none needed day-1)
- The `__KMS_REF__` marker migration (nothing to migrate, no raw rows written)
- Capabilities.yaml schema (P3 generalizes the manual install contract)
- The `install-opendesign` skill body (P3's P3-WP5 deliverable)

---

## 8. OPS-lane compliance statement (AC-6 — MET)

The install was performed **entirely from `~/services/opendesign/`** (the ops lane):

- **Zero execution from the planning worktree** (`/home/nea/ensemble-src-wt-designer-agent-design`). No `npm install` was ever run inside the worktree. No binaries were copied into the worktree.
- **All install operations wrapped in `/tmp/od-ops.sh`** — a standalone `#!/bin/bash` wrapper that unsets ambient `POSTGRES_*` / `PG*` env vars before any work and `echo`-verifies ZERO survivors before passing through to the underlying command. (Pattern mandated per the 3 prior live-probe incidents; this run did not touch any ensemble daemon or DB.)
- **No ensemble daemon was started, stopped, or probed.** Verified: no commands like `curl 9797`, `curl 7979`, `curl 8079` were issued; no `systemctl` calls; no `pgrep ensemble` queries; no DB connections.
- **No `git` operations against the worktree from this turn.** All install was in `~/services/opendesign` only.

---

## 9. Files touched (this WP's deliverable)

- `implementation-plan/verdicts/p2-wp4-od-install-notes.md` — **THIS FILE** (verdict)
- (no other planning-tree files modified)
- `~/services/opendesign/` — ops-lane install dir (outside worktree, not part of the planning tree)

Commit: `docs(designer): P2-WP4 OpenDesign install evidence (best-effort, daemon-blocked)` — P2-WP4 id in body. Commit refs the verdict file only.

---

## 10. Recommendation to P3-WP5 (installer skill)

When P3 builds `install-opendesign`:

1. **Check Docker availability first** — if absent on a Linux host, fail-fast with the exact message in §6.1 (don't try to fall back to source-build unless `--allow-source-build` is passed)
2. **MCP install step = idempotent `npm install open-design-mcp@<pinned>`** into the chosen ops-lane dir; verify the binary at the expected path
3. **Daemon install step = `docker compose up -d`** with the env from `deploy/.env.example`; healthcheck on `GET http://127.0.0.1:7456/api/health` per `docker-compose.yml` (already declared as the `healthcheck.test`)
4. **Registration step = call the daemon's `mcp_servers` endpoint with the values in §5.1**; emit the `install-audit.jsonl` line per arch §7.4
5. **No KMS-Lite mint needed for day-1 loopback** — record credential posture = "none" in the install-audit; record `OD_DISABLE_API_AUTH=1` if the deployer chose the escape hatch
6. **Versioning = pin both `open-design` daemon image and `open-design-mcp` npm version** in `capabilities.yaml#schema_version` (per arch §7.3)