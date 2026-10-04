# Phase A Plan — chart-image-delivery (render-at-validation capture)

Date: 2026-10-04 (R1 05:01 / R2 05:55)
Author: planner[v2] via plan-creation worker
Status: Draft R2 — Revision loop 2 (R1-R9 fold-in)
Feature commission: `chart-image-delivery` (D1 self-install, D2 local render, D3 tmp_images substrate, D4 per-platform native delivery)

> **Precedence:** `architecture-recommendation.md` §3 governs over phase-plan prose on conflict.

> **Adjudication status (R2):** R1 NEEDS-REVISION verdict folded. R6 (ari pre-warm) ratified. R9 amendments #15, #16, #17, #18, #19 folded into this file in place; #14 left to Phase B (do-not-contradict only). §7 unverified item (nvm which 24 vs nvm-exec) resolved in §4 below. Pin-label correction + content-addressable grep migration applied.

## Objective

Charter (the Mermaid agent) renders the validated diagram to a PNG, persists it to the daemon's `tmp_images` substrate, and emits a canonical source-agnostic image-reference marker in its final response. The marker survives verbatim passthrough through `generate_chart` into the parent agent's final response so the chat-source dispatcher (Phase B) can strip and upload the PNG to Discord / Slack / Telegram. The validated Mermaid text remains the primary deliverable; the PNG is an attachment layered on top — text chart delivery must never break because of image-render problems.

## Scope

### In Scope

- `agents/charter/workflow.md` Step 5 (validation, :89–125) extended to render a PERSISTED PNG during validation, replacing the throwaway SVG path.
- `agents/charter/rule.md` updated to canonically define the marker contract and the degradation rule (Mermaid text delivery survives all image-render failures).
- `agents/charter/soul.md` updated to state the new image-attachment role (one-line addition in "My Principle" — keep terse).
- `agents/charter/meta.json` `tools.allow` += `"image"` (gives charter `image_save` / `image_get` / `image_list`; `_auth.py:38` auto-implies `image-reader` as a team_member).
- `agents/charter/skills-template/install-mermaid-cli.md` — new install skill modeled on `install-opendesign.md` v1.3.0 (job f9946b1d lineage): idempotent, fenced (no Docker / no apt / no system Node), nvm user-space detection+bootstrap, absolute-path capture for non-interactive bash, reversal + verify sections.
- `agents/_prompt_system/innate-skills/chart/skill.md` — minor: state the new "marker in result" contract in the `generate_chart()` description (one paragraph; keeps sub-agents from stripping the marker).
- `decisions.md` — author the INITIAL cross-cutting decision record (this plan's companion artifact). D1–D4 verbatim, marker spec, capture contract, HTTP-API behavior, degradation inputs for Phase B, restart/promote matrix.

### Out of Scope

- Phase B (dispatcher marker extraction + per-adapter native upload + fallbacks).
- Phase C (agent-guidance updates running on the parallel worker lane).
- Phase D (cross-cutting test/e2e hardening + version/docs).
- Any `daemon/` code change — `generate_chart` keeps its verbatim-passthrough property (`daemon/tools/chart_tools.py:525`); the marker is emitted by charter inside its own assistant turn, not stamped by the tool.
- `daemon/tools/_auth.py` change — `image` category already exists with the right `TOOL_REQUIRED_AGENTS` mapping (`:38`).
- `daemon/services/tmp_image_store.py` and `daemon/services/tmp_image_cleanup_service.py` — already implement D3 requirements (verified: hourly cadence, 30-day retention, always-on, two-pass blob+sidecar pair, `protected` exempt — see decisions.md §D3).
- New agent category, new tool category, new team_member, new HTTP route, new daemon restart/promote.
- Any mermaid / puppeteer / chromium code change.

### Files Touched (exact list)

| File | Change class | Restart needed? |
|------|--------------|-----------------|
| `agents/charter/workflow.md` | extend Step 5 render + persist; **sources `install-mermaid-cli.lib.sh`**; pre-render sanitizer; `ulimit -v` + `timeout 60` wrapper; retire `npx -y` (was `:109/:141`); READINESS_PROBE invocation | no (picked up at next charter spawn) |
| `agents/charter/rule.md` | +1 Must (marker only after valid `image_save`); +1 Never (no retry on render-side failures); syntax-retry budget preserved (3 attempts) | no |
| `agents/charter/soul.md` | +1 sentence in "My Principle" | no |
| `agents/charter/meta.json` | `tools.allow += "image"` | no (picked up at next charter spawn) |
| `agents/charter/skills-template/install-mermaid-cli.md` | NEW file — install skill, fenced, idempotent, **`## READINESS_PROBE`** section, **HONEST-STOP prereqs** | no (skill_seed_service consumes on next spawn) |
| `agents/charter/skills-template/install-mermaid-cli.lib.sh` | NEW file — extracted pure-bash + jq library sourced by `workflow.md` Step 5 (single source of truth for the probe; satisfies the agent-prompt-only fence) | no |
| `agents/_prompt_system/innate-skills/chart/skill.md` | +1 paragraph on marker preservation (seeded here; Phase C extends to all 21 chart consumers) | no |
| `.agents/shared/context.md` | +1 one-line reminder: "ari/commissioner pre-warm the mermaid-cli toolchain as a deploy step on every fresh host (mirrors `install-opendesign` deploy-step execution)" (R6 ratified) | no (planning artifact) |
| `agents/ari/workflow.md` | +1 note in commissioning workflow: when a new project is provisioned, the mermaid-cli toolchain is part of the deploy-step pre-warm checklist (R6 ratified) | no |
| `.agents/shared/planning/chart-image-delivery/decisions.md` | NEW file (companion) | n/a — planning artifact |

No `daemon/`, `tests/`, or `frontend/` edits in Phase A. No migrations, no SQL, no new HTTP routes.

## Components

### 1. Charter validation extension (workflow.md Step 5)

Replace the throwaway SVG render (`-o /tmp/charter_validate_output.svg`, `rm -f` at :117) with a PERSISTED PNG render + `image_save` tool call. The validation script at `agents/charter/workflow.md:89-125` becomes:

```bash
# 0. READINESS_PROBE — single source of truth lives in
#    agents/charter/skills-template/install-mermaid-cli.lib.sh (extracted pure
#    bash + jq library). 4-signal probe replaces `command -v mmdc` (that probe
#    gives a PERMANENT false-cold in non-interactive bash because ~/.bashrc
#    is interactive-only and is never sourced). See arch-rec §3 amendment #17
#    and Components §4 below for the 4-signal contract.
#
#    Returns 0 on warm, 1 on cold (charter self-heal path), 2 on
#    install-in-progress-other (degrade now). Probe write-path also accepts
#    EITHER `nvm which 24` OR `nvm-exec` idiom — arch-rec §7 unverified
#    item resolved here.
. agents/charter/skills-template/install-mermaid-cli.lib.sh
charter_readiness_probe || {
  rc=$?
  if [ $rc -eq 2 ]; then
    echo "INSTALL_IN_PROGRESS_OTHER — degrading, no marker"
    # fall through to text-only return (existing Step 8 path)
  else
    echo "COLD — invoking install-mermaid-cli skill"
    # flock-guarded self-heal (arch-rec #18); 10s timeout
    flock -w 10 "$HOME/.cache/charter/mermaid-install.lock" \
      -c "invoke install-mermaid-cli skill" || {
        # Lock-contended OR install ran long → write async queue marker
        # and degrade this turn (multi-minute chromium download must NOT
        # ride generate_chart's 600s budget)
        : > "$HOME/.cache/charter/mermaid-pending-install"
        echo "INSTALL_DEFERRED — async queue, degrading this turn"
      }
  fi
}

# 1. Create a unique temp file for this instance
TMPFILE=$(mktemp /tmp/charter_XXXXXX.mmd)
TMPPNG=$(mktemp /tmp/charter_XXXXXX.png)   # persisted target
TMPSVG=$(mktemp /tmp/charter_XXXXXX.svg)   # intermediate (kept until PNG)
TMPCFG=$(mktemp /tmp/charter_XXXXXX.cfg)   # puppeteer config

# Defensive cleanup — extended to include the .svg artifact (Focus 5 F8/F11:
# temp-file hygiene; arch-rec §3 amendment noted at :113). The trap fires on
# ANY exit, including render failure, image_save failure, and signal exit.
trap 'rm -f "$TMPFILE" "$TMPPNG" "$TMPSVG" "$TMPCFG"' EXIT

# 2. Write the Mermaid content to it
cat > "$TMPFILE" <<'EOF'
flowchart TD
    A[User] --> B[Auth Service]
    ...
EOF

# 3. PRE-RENDER SANITIZER (arch-rec #15 / F4):
#    mmdc reads `%%{init}%%` directives and frontmatter securityLevel keys
#    from inside the .mmd body, overriding the -c securityLevel pin.
#    Strip both. A 3-line sed pass: drop the init block, drop the
#    frontmatter YAML key. Charter is told the diagram source is trusted
#    so this is defense-in-depth, not a validator.
sed -i -E \
  -e '/^%%\{init\}%%$/,/^%%\{init\}%%$/d' \
  -e '/^[[:space:]]*securityLevel:[[:space:]]*/d' \
  "$TMPFILE"

# 4. Resolve installed global mmdc by ABSOLUTE PATH (arch-rec #16: retire
#    `npx -y @mermaid-js/mermaid-cli` — unpinned remote fetch-and-execute on
#    every render; tsc-typosquat hygiene. After D1 install the absolute path
#    is in ~/.config/charter-mermaid-puppeteer.json; the probe populates the
#    $MMDC_BIN env var for this shell.
#
#    The `nvm which 24` line is the canonical idiom (install-opendesign.md:859);
#    the `nvm-exec` line is the fallback (decisions.md:18). Probe accepts
#    either, per arch-rec §7.
NVM_NODE="$($HOME/.nvm/nvm-exec which 24 2>/dev/null \
            || { [ -s "$HOME/.nvm/nvm.sh" ] && \. "$HOME/.nvm/nvm.sh" \
                  && nvm which 24; })"
MMDC_BIN="$MMDC_BIN"   # exported by the readiness probe from the config file

# 5. Write the puppeteer config (executablePath read from the install's
#    recorded path; NOT trusted from config-write time — re-probed at render
#    time per arch-rec #17 amendment)
cat > "$TMPCFG" <<EOF
{"executablePath": "$PUPPETEER_EXECUTABLE_PATH", "args": ["--no-sandbox"]}
EOF

# 6. Render the PNG. Wrapper = timeout 60 (wall) + ulimit -v 2GB (memory)
#    (arch-rec #15 / F4 — wall-clock bound does not bound memory).
#    -c pins securityLevel:strict + htmlLabels:false from the CLI side
#    (the in-diagram override is sanitized in step 3).
#    --no-sandbox: arch-rec #15 sandbox policy — never root; --no-sandbox
#    only on launch failure, logged. Single-user host residual; if launch
#    fails without --no-sandbox, the next render retries WITH --no-sandbox
#    and writes a WARN to the charter validation log.
( ulimit -v 2097152; timeout 60 "$NVM_NODE" "$MMDC_BIN" \
    -i "$TMPFILE" \
    -o "$TMPPNG" \
    -t default -b white -w 1200 -s 2 \
    -c '{"securityLevel":"strict","htmlLabels":false}' \
    --puppeteerConfigFile "$TMPCFG" \
    --quiet \
  ) 2>&1

RENDER_EXIT=$?

# 7. Inspect the result. SYNTAX failures keep the existing 3-attempt
#    retry budget (rule.md:14, syntax ONLY). Render-side failures NEVER
#    retry (arch-rec #19 / Never rule): no marker, text-only Mermaid,
#    log line. Pre-existing rule.md syntax-retry budget is preserved.
if [ $RENDER_EXIT -eq 0 ]; then
  echo "VALIDATION OK"
else
  echo "VALIDATION FAILED"
  rm -f "$TMPPNG"
  exit 0   # proceed to step 8 with the validated Mermaid text only
fi
```

After step 7 succeeds, the next step in the workflow (new step 8) is the **persist + marker emit** flow. Charter base64-encodes the PNG bytes inline and invokes the `image_save` tool:

```python
# Step 8 — persist + extract image_id (LangChain tool call, not bash).
# Arch-rec #19 MUST rule: emit marker ONLY after a valid image_save
# result. If image_save returns "Error: ..." the marker is NEVER
# appended — text-only Mermaid delivery, no marker.
import base64
with open("/tmp/charter_XXXXXX.png", "rb") as f:
    png_b64 = base64.b64encode(f.read()).decode("ascii")

# Optional store-capacity pre-check (arch-rec Focus 4.6, 🟢): if the
# current chart-render footprint is > 80% of the cap, skip the persist
# entirely — text-only Mermaid, `image_store_full` log line. Saves a
# wasted render. The cap is 1 GiB by default (tmp_image_store.py:140-148).
if image_store_chart_render_usage_under_threshold():
    result_json = image_save(
        content_b64=png_b64,
        content_type="image/png",
        feature="chart-render",
        retention_class="normal",
        # source_agent auto-stamped by the tool itself (image_tools.py:712-715)
    )
else:
    # log image_store_full, fall through to text-only delivery
    pass

# image_save returns JSON: {"image_id": "<32hex>", ...}
# — or "Error: ..." on failure (never raises, per image_tools contract)
```

Step 9 emits the marker alongside the validated Mermaid text, ONLY when the image_save result is a valid JSON containing an `image_id`. The existing "Return diagrams in ```mermaid fenced code blocks" rule (rule.md:9, :24) is preserved: the mermaid block stays first and byte-identical; the marker is appended on its own line AFTER the explanation. **Per arch-rec #19 / Must rule**: the marker is conditional on a valid image_save result; any "Error: ..." response short-circuits to text-only.

**Exact mmdc flags (capture contract — see decisions.md for full rationale; arch-rec #15 security pins folded here):**

| Flag | Value | Why |
|------|-------|-----|
| `-t` | `default` | Mermaid default theme; matches what most chat previews show. |
| `-b` | `white` | Opaque background — transparent PNGs look bad in dark-mode chat UIs. White is the safe default; per-platform theming is Phase B's job, not ours. |
| `-w` | `1200` | Width in px. Fits Discord embed preview (≤ 800) and Slack unfurl width; scales down for Telegram. |
| `-s` | `2` | Scale factor (2x retina) — keeps text crisp on high-DPI screens. |
| `-c` | `'{"securityLevel":"strict","htmlLabels":false}'` | **arch-rec #15 / F4** — CLI-side pin; the .mmd-side override is sanitized in step 3. `strict` disables click events; `htmlLabels:false` neutralizes HTML-injection in labels. |
| `--puppeteerConfigFile` | `$TMPCFG` | Explicit chromium path + `--no-sandbox` arg. The install skill writes the executablePath to `~/.config/charter-mermaid-puppeteer.json`; the readiness probe re-probes it at probe time (arch-rec #17). |
| `ulimit -v` | `2097152` (KB ≈ 2 GB) | **arch-rec #15 / F4** — memory bound. Wall-clock `timeout 60` does not bound memory; puppeteer can OOM. |
| `timeout` | `60 s` | Wall-clock budget. Puppeteer cold start is ~5–10s on warm cache; 60s leaves headroom without exploding the validation window. |

The existing **3-attempt syntax-retry** budget (rule.md:14) is preserved for **syntax** errors only. Render-side failures (puppeteer timeout, image_save error, store-full, file-system errors) do NOT retry (arch-rec #19 / Never rule) — they fall through to the degradation branch in step 9 with no marker.

**mktemp hygiene** — every temp file uses `mktemp /tmp/charter_XXXXXX.{mmd,png,svg,cfg}`; the SVG is an intermediate artifact (kept until PNG is read by `image_save`); cleanup is at the end of the render block AND in a defensive `trap 'rm -f "$TMPFILE" "$TMPPNG" "$TMPSVG" "$TMPCFG"' EXIT` so an early-exit (signal, render failure, image_save failure) does not leak. (The existing cleanup at :117 is replaced with the new `rm -f` set; arch-rec #15 + F8/F11 hygiene note.)

### 2. Charter enablement (meta.json + image category)

`agents/charter/meta.json:11` `tools.allow` array currently has no `image` entry. Adding it gives charter the entire `image` category — `image_save` (write), `image_get` / `image_list` (read), plus `explain_image` (vision) which it does not need but is harmless.

**Side effect (verified):** `daemon/tools/_auth.py:38` `TOOL_REQUIRED_AGENTS["image"] = ["image-reader"]` — adding `image` to charter's `tools.allow` implicitly grants `image-reader` as a team member (the single source of truth at `_auth.py:9-19` makes `tools.allow` the canonical authorization for tool→agent spawns). No explicit `team_members` edit needed.

**Provenance** (per decisions.md §D3): `feature="chart-render"`, `retention_class="normal"`. The tool auto-stamps `source_agent` from the active instance (image_tools.py:712-715 — confirmed in spot-check).

**Version bump:** `meta.json:7` `version` advances `1.1.0 → 1.2.0` (new capability = minor bump, semver). Documented in decisions.md §restart-promote.

### 3. Canonical marker contract (decisions.md §marker)

One canonical syntax, source-agnostic, survives verbatim passthrough through `generate_chart`:

```text
<!-- ens-img:chart-render:<image_id> -->
```

| Field | Value | Why |
|-------|-------|-----|
| Prefix | `<!-- ens-img:` | HTML comment — invisible in Markdown renderers, Discord/Slack/Telegram strip or ignore, the LLM prompt easily tells the agent to pass it through unmodified. |
| Feature tag | `chart-render` | Distinguishes chart PNGs from other tmp_image sources (clipboard, screenshot, designer output). Phase B dispatcher keys off this. |
| Image id | `<image_id>` | 32-hex (uuid4 — verified at `daemon/services/tmp_image_store.py:24-27` enforced at the router layer + image_save). |
| Suffix | ` -->` | Close the comment. |

**Lifecycle (the contract Phase B consumes):**

1. Charter validates + renders + persists PNG → gets `image_id` from `image_save` JSON result.
2. Charter appends `<!-- ens-img:chart-render:<image_id> -->` to its assistant turn, on its own line AFTER the explanation prose.
3. `generate_chart` returns the result verbatim (`daemon/tools/chart_tools.py:525` — pinned property).
4. Parent agent includes the marker in their final response (the chart innate skill already instructs callers to "paste the result directly without re-wrapping"; Phase A adds one sentence: "do not strip the trailing `<!-- ens-img:... -->` marker").
5. Chat-source dispatcher (Phase B) extracts the marker, strips it from the visible message text, fetches the PNG via the `image_id` (HTTP `GET /api/tmp_images/<id>`), uploads via the platform-native attachment API.
6. Marker that does not parse (typo, missing `chart-render` tag) is ignored by Phase B — text delivery is unaffected.

**HTTP-API caller behavior (non-chat, e.g. tester / web UI direct /api/messages):** the marker remains in the assistant text content. Clients that want the PNG can parse the marker and call `GET /api/tmp_images/<id>`. Clients that don't know the marker just see the validated Mermaid text (no rendering artifact broken — the marker is a single line of `<!-- ens-img:chart-render:abc... -->` at the bottom). This preserves the verbatim-passthrough property without forcing the daemon to invent a second content channel.

**What charter does NOT do in Phase A:**

- Does NOT upload the PNG. That is Phase B's job, per platform.
- Does NOT strip the marker from the text (it appends it; stripping is Phase B).
- Does NOT emit any URL — the marker is a reference, not a link. (Phase B may also emit a fallback `https://...` URL in chat, but that is its decision, not Phase A's.)

### 4. D1 install skill (install-mermaid-cli.md) + extracted lib

Two artifacts ship in tandem (arch-rec #17 / #18):

1. **`agents/charter/skills-template/install-mermaid-cli.md`** — the install SKILL, modeled on `install-opendesign.md` v1.3.0. Loaded by the skill_seed_service on next spawn; charter invokes it on cold-detect.
2. **`agents/charter/skills-template/install-mermaid-cli.lib.sh`** — the extracted pure-bash + jq LIBRARY, sourced by `workflow.md` Step 5. Single source of truth for the readiness probe (the skill is loaded only on cold-detect; the lib is sourced on EVERY render). Pure bash + jq — fits Phase A's agent-prompt-only fence (no compiled deps, no daemon code).

**Pattern source:** `agents/worker/skills-template/install-opendesign.md` v1.3.0 (verified via spot-check: `version: 1.3.0` in frontmatter; full body 1237 ln; fences at :796; nvm bootstrap at :852-895 with `NVM_NODE=$(nvm which 24)` absolute-path capture; durable run at :897; verify at :937; reversal at :964).

**Fences (verbatim from install-opendesign.md:796, abridged for mermaid):**

- NO Docker (same fence).
- NO apt / system packages. Every native dep resolves via prebuilt binaries (chromium, mmdc JS closure).
- No plaintext secrets (mmdc has none — pure toolchain install).
- Idempotent — re-running on a fully-working host is a verify-only fast path.
- HONEST-STOP on hard blockers — if nvm bootstrap fails (e.g. no curl), report and stop; do NOT apt-install.

**HONEST-STOP prereqs (arch-rec #18, mandatory gate — any absent → HARD_BLOCKER, never auto-install):**

- `curl` (nvm bootstrap)
- `git` (chromium download)
- `jq` (config-file probe — required by the lib's READINESS_PROBE)
- `~/.nvm/nvm.sh` present and parseable (Node version detection)

These are checked at install-skill entry. If any is missing, the skill emits a structured `HARD_BLOCKER: <missing prereq>` envelope and stops. No retries, no apt-install, no fallback.

**Sections to ship (mirroring install-opendesign.md order; `## READINESS_PROBE` is new per arch-rec #17):**

1. Pre-flight detect — `command -v nvm`, `~/.nvm/nvm.sh` presence, `nvm which 24` (OR `nvm-exec which 24` per arch-rec §7 unverified-item resolution), `command -v mmdc`, chromium at `~/.cache/puppeteer/chrome/`. Probes accept EITHER `nvm which 24` (install-opendesign.md:859 canonical) OR `nvm-exec` idiom (decisions.md:18 fallback). (Equivalent of install-opendesign.md's pre-flight before line 854.)
2. nvm bootstrap — `curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.8/install.sh | bash`, `. "$NVM_DIR/nvm.sh"`, `nvm install 24`, `NVM_NODE="$(nvm which 24)"` (absolute path capture; same as install-opendesign.md:855-859).
3. Global `mmdc` install via `npm i -g @mermaid-js/mermaid-cli@12` under the nvm-managed Node (NOT system node). Records the absolute `mmdc` path in `~/.config/charter-mermaid-puppeteer.json` for non-interactive use.
4. Chromium resolution — `find ~/.cache/puppeteer -name 'chrome' -type f` returns the cached binary; the install procedure records the path in `~/.config/charter-mermaid-puppeteer.json` (same config file). Idempotent re-entry: partial chromium present → resume from where it stopped.
5. **Non-interactive Node resolution helper** (NEW compared to install-opendesign.md): a tiny shell snippet that bash-launched processes (daemon-spawned, no TTY, no `.bashrc` source) can use to load nvm and find `node` + `mmdc`. Pattern: `[ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"` at the top of the render script, OR absolute-path capture upfront and pass it as an env var. **Reason:** confirmed at `~/.bashrc:121-123` that nvm is interactive-only — `npx -y` works today purely because the warm cache `~/.npm/_npx/668c188756b835f3/` survives, but that cache is **evictable**, and the install skill MUST close that hole.
6. **Verify** — `mmdc --version` returns `12.0.0`; a minimal `flowchart TD\n  A-->B` renders to BOTH `out.svg` and `out.png`; both files non-empty; `file out.png` reports `PNG image data`. Capture observed install time → write `pinned.observedInstallSec` to the config file (for the inline-install 60s cap heuristic; arch-rec §7 unverified item).
7. **Reversal** — `npm rm -g @mermaid-js/mermaid-cli`, `nvm uninstall 24`, `rm -rf ~/.cache/puppeteer` (charter-owned; safe to clear). The config file at `~/.config/charter-mermaid-puppeteer.json` is also removed.
8. **`## READINESS_PROBE` (NEW per arch-rec #17, lives in BOTH the skill body and the extracted lib)** — pure bash + jq, 4-signal contract. Single source of truth (lib.sh exports the function; skill body documents the behavior).

**The 4-signal READINESS_PROBE (arch-rec #17 — replaces `command -v mmdc`):**

| # | Signal | Source | Why `command -v mmdc` fails |
|---|--------|--------|----------------------------|
| 1 | `~/.config/charter-mermaid-puppeteer.json` exists + is valid JSON | the install's own config file | `command -v` consults `$PATH`, which non-interactive bash does not populate with nvm |
| 2 | `mmdcPath` (from config) is an absolute path + executable | jq + `[ -x ]` | nvm shim lives in `~/.nvm/versions/.../bin/`, NOT in the daemon-spawned `$PATH` |
| 3 | `puppeteerConfig.executablePath` (from config) is an absolute path + executable **probed at probe time** (not trusted from config-write time) | `[ -x ]` at probe time | chromium may have moved (cache evict, manual edit, version upgrade); probing on every render survives moves |
| 4 | pinned `mermaidCli` version matches (config.mermaidCli == 12) | jq equality | mmdc v11 changed the `-c` syntax; mmdc v13 may change again. Pin the major. |

The probe returns 0 (warm), 1 (cold — invoke install skill), or 2 (install-in-progress-other — degrade now, log `install_in_progress_other`). The lib's function is sourced by `workflow.md` Step 5 (see Components §1 step 0). The skill body documents the same contract and the path from cold → invoke → probe-again.

**Hybrid executor (arch-rec #18 — replaces the old "charter self-installs" framing):**

The install is NOT solely charter-driven. Two execution paths converge on the same install procedure:

1. **Pre-warm path (preferred, deploy-step):** `ari` / `commissioner` invokes the `install-mermaid-cli` skill as a deploy step when a new project is provisioned (mirrors `install-opendesign` deploy-step execution). This is OWNED by ari's commissioning workflow (R6 ratified — one-line reminder added to `.agents/shared/context.md` + a note in `agents/ari/workflow.md`). When pre-warm runs, charter always sees a warm probe on first invocation.
2. **Self-heal path (charter cold-detect, advisory `flock`):** when charter's READINESS_PROBE returns 1, charter attempts the install behind an **advisory `flock`**:
   - Lock file: `$HOME/.cache/charter/mermaid-install.lock`
   - Timeout: 10s (advisory; advisory means charter does not block long)
   - Lock-contended → log `install_in_progress_other`, write async queue marker `$HOME/.cache/charter/mermaid-pending-install`, degrade this turn (text-only Mermaid, no marker, no retry). The OTHER charter completes the install; the next render re-probes warm.
   - Inline install allowed ONLY when (a) `cold_misses_in_session < 2` (state held in `$HOME/.cache/charter/mermaid-session-state.json`) AND (b) chromium partially present (resumable) AND (c) hard 60s cap. The 60s cap is the heuristic referenced in arch-rec §7 unverified — `pinned.observedInstallSec` tunes it later.
   - Cold-detect mid-turn writes `$HOME/.cache/charter/mermaid-pending-install` (async queue marker) and degrades. Subsequent turns re-probe.

**`install-mermaid-cli.lib.sh` contract (NEW, arch-rec #17):**

- Pure bash + jq. No compiled deps.
- Exports `charter_readiness_probe` (returns 0/1/2).
- Exports `MMDC_BIN` and `PUPPETEER_EXECUTABLE_PATH` env vars on success.
- Sourced by `workflow.md` Step 5 step 0 (`. agents/charter/skills-template/install-mermaid-cli.lib.sh`).
- Idempotent: sourcing the lib on every render is a cheap jq-read + stat-walk.
- Fits Phase A's agent-prompt-only fence (no daemon code change; lib is agent-owned, lives under `agents/charter/skills-template/`).

**First-cold-render guidance (arch-rec #18, OPTIONAL item):**

A cold chromium download is multi-minute (puppeteer downloads ~150 MB chromium on first install). This MUST NOT block inside `generate_chart`'s 600s `invoke_and_wait` budget. The hybrid executor's flock-guarded self-heal + async queue marker is the structural answer: the install runs in the background or on the next pre-warm, never inside the tool's blocking call. Charter's render step returns "degraded this turn" on cold-detect with a `cold_install_deferred` log line; the user gets text-only Mermaid immediately, and the next render (likely minutes later) is warm.

**Trigger in workflow.md:** Step 5's step 0 (see Components §1) sources the lib and runs the probe. On `rc=1` the install skill is invoked; on `rc=2` the current turn degrades; on `rc=0` the render proceeds.

### 5. D3 verification (decisions.md §D3)

The `tmp_images` substrate already implements D3. Spot-checked:

- `daemon/services/tmp_image_cleanup_service.py:74` `DEFAULT_TMP_IMAGE_CLEANUP_INTERVAL_SECONDS = 3600` (hourly).
- `:80` `DEFAULT_TMP_IMAGE_CLEANUP_RETENTION_DAYS = 30` (30-day retention).
- `:13-25` "ALWAYS ON (no kill-switch)" — only operator knobs are the interval + retention; no `enabled=False` production path.
- `:37-42` pair semantics — blob+sidecar deleted as a pair, `FileNotFoundError` swallowed, orphans reaped by their own mtime.
- `:470-498` `_is_protected` — only literal `retention_class == "protected"` exempts from sweep. The cap still counts protected bytes (architect amendment #3 — `tmp_image_store.py:107`).
- `tmp_image_store.py:89-91` `_VALID_RETENTION_CLASSES = frozenset({"normal", "protected"})`.

**Conclusion for D3:** `retention_class="normal"`, `feature="chart-render"` is the correct stamp. No protected-class justification — chart PNGs are transient chat attachments, not design baselines. No sweep-config change. Phase A ships zero changes to either service.

### 6. Test strategy (Phase A only)

**Do-not-break pin audit (must be GREEN before merge; arch-rec Reviewer R9 optional pin-label correction folded here — original R1 plan mislabeled `skill.md:74` as `_BUSY_STRING` when it is actually the `_PAUSED_STRING` site):**

| Pin | String | Sites (byte-stable, content-addressable) |
|-----|--------|-------------------------------------------|
| `_BUSY_STRING` | `"Error: Charter busy; pass fresh=True for parallel charts."` | `daemon/tools/chart_tools.py:55` (`_BUSY_MSG` definition), `tests/test_chart_tools.py:296` (test constant), `tests/test_chart_tools.py:540, 611` (assertion sites), `agents/_prompt_system/innate-skills/chart/skill.md:62` + `:68` (recovery-ladder docs; grep -F for the exact string) |
| `_PAUSED_STRING` | `"Error: Charter is paused; resume it or pass fresh=True for a new charter."` | `daemon/tools/chart_tools.py:222` (definition site — note: the `chart_tools.py:222` label is a paused-string label, NOT a busy-string label), `tests/test_chart_tools.py:298` (test constant), `agents/_prompt_system/innate-skills/chart/skill.md:74` (source-of-truth note; grep -F for the exact string) |
| `Wedged-Charter Recovery` heading | — | `agents/_prompt_system/innate-skills/chart/skill.md:66` heading (grep -F for `## Wedged-Charter Recovery`) |

**Coordination with Phase C (`agents/_prompt_system/innate-skills/chart/skill.md` is shared):** all assertions about that file MUST be content-addressable greps, NOT line numbers. Line numbers false-fail when Phase C inserts content above. The pin audit and the new test cases below use `grep -F` for the EXACT busy/paused strings and the `## Wedged-Charter Recovery` heading. The reviewer pass (task T14) runs the greps on the final file, not a line-by-line diff.
- `tests/test_chart_tools.py:540, 611` asserts the busy string is returned by the rejection path. Phase A does not touch this path.
- `tests/test_chart_tools_legacy_error_contract.py` pins the "Error: ..." return shape for the legacy fresh-path raise. Phase A does not touch this path.
- `daemon/tools/chart_tools.py:525` verbatim passthrough is the load-bearing property. Phase A does not touch this code; charter emits the marker inside its OWN assistant turn, not via a tool-side transformation. The passthrough test (if any) remains green by construction.

**New unit tests (under `tests/test_charter_render_capture.py`):**

1. `test_charter_workflow_persists_png_on_success` — stub `image_save` to return a fixed `image_id`; assert the rendered marker matches `<!-- ens-img:chart-render:<image_id> -->`. Per arch-rec #19: marker only after a valid `image_save` result.
2. `test_charter_workflow_no_marker_on_image_save_failure` — `image_save` returns `"Error: store not initialized"`; assert the result contains the Mermaid block but NO `<!-- ens-img:` marker. Per arch-rec #19: error response short-circuits to text-only.
3. `test_charter_workflow_no_marker_on_render_failure` — `mmdc` exits non-zero; assert the Mermaid block is present (validation success is separate from render success) and no marker.
4. `test_charter_workflow_no_marker_on_retry_exhausted` — all 3 syntax retries fail; assert the Mermaid text is returned with the "validation skipped" warning AND no marker. Per arch-rec #19: syntax retry budget is preserved; render-side failures do NOT retry.
5. `test_charter_workflow_marker_is_byte_stable` — render multiple charts; assert the marker regex is byte-identical for each (no LLM variance).
6. `test_charter_meta_json_includes_image_category` — `tools.allow` contains `"image"`.
7. `test_install_skill_idempotent_on_warm_cache` — second invocation of the install skill on a host with mmdc already installed is a no-op (verify only).
8. `test_install_skill_handles_missing_nvm` — invocation on a host with no `~/.nvm` runs the bootstrap and reports success.
9. `test_readiness_probe_warm_returns_zero` — all 4 signals present in `~/.config/charter-mermaid-puppeteer.json` + valid; `charter_readiness_probe` returns 0 and exports `MMDC_BIN` + `PUPPETEER_EXECUTABLE_PATH`. (NEW — arch-rec #17.)
10. `test_readiness_probe_cold_returns_one_and_invokes_install` — config file missing; probe returns 1; charter invokes install-mermaid-cli; probe is run again; on success render proceeds. (NEW.)
11. `test_readiness_probe_install_in_progress_returns_two` — another charter holds the install lock; probe returns 2; charter degrades this turn. (NEW — arch-rec #18.)
12. `test_hybrid_executor_prewarm_renders_warm_first_try` — simulate ari deploy-step pre-warm running; charter's first `generate_chart` call has a warm probe. (NEW — arch-rec #18 / R6.)
13. `test_render_security_pins_strict_html_false` — assert the mmdc invocation includes `-c '{"securityLevel":"strict","htmlLabels":false}'`. (NEW — arch-rec #15 / F4.)
14. `test_render_pre_sanitizer_strips_init_and_frontmatter` — feed a `.mmd` with `%%{init}%%` and a frontmatter `securityLevel:`; assert the sed sanitizer strips both before render. (NEW — arch-rec #15 / F4.)
15. `test_render_ulimit_and_timeout_wrap_invocation` — assert the mmdc call is wrapped in `( ulimit -v 2097152; timeout 60 … )`. (NEW — arch-rec #15 / F4.)
16. `test_npx_y_retired_from_workflow` — grep `agents/charter/workflow.md` for `npx -y @mermaid-js` and assert ZERO matches (per arch-rec #16). (NEW.)
17. `test_store_capacity_precheck_skips_persist_above_80_percent` — mock `image_list` to return a footprint > 80% of cap; assert `image_save` is NOT called and the `image_store_full` log line is emitted. (NEW — arch-rec Focus 4.6, 🟢.)
18. `test_pin_audit_greps_pass` — run `grep -F` for the exact busy/paused strings and the `Wedged-Charter Recovery` heading in their respective files; assert all present, byte-identical. (NEW — replaces the R1 line-number audit that false-fails on Phase C inserts.)

**Charter-side verification (manual + automated in CI):**

- Run the full `tests/test_chart_tools.py` suite — must be 100% green.
- Run the pin-audit greps (`grep -F`) on `daemon/tools/chart_tools.py`, `tests/test_chart_tools.py`, and `agents/_prompt_system/innate-skills/chart/skill.md` — all four pins must be byte-identical.
- Spawn a fresh charter instance; submit a `generate_chart` request via the API; assert the response contains BOTH a `\`\`\`mermaid` block AND a `<!-- ens-img:chart-render:... -->` marker; assert `image_list(feature="chart-render")` returns at least one row.
- Spawn a fresh charter; force a `mmdc` failure (chmod 000 the temp dir); submit; assert response contains the Mermaid block but NO marker; assert `image_list(feature="chart-render")` is empty for this chart's `source_agent`.
- Cold-detect scenario: delete `~/.config/charter-mermaid-puppeteer.json`; submit; assert charter's READINESS_PROBE returns 1, the install skill is invoked, the probe re-runs warm, and the render proceeds on the retry. Verify the async queue marker `$HOME/.cache/charter/mermaid-pending-install` is created mid-turn and cleaned up on the next warm probe.
- Pre-warm scenario: invoke the install-mermaid-cli skill via the ari commissioning path; submit; assert the first render is warm with no self-heal invocation.

### 7. Implementation dispatch shape

All Phase A work touches a single bounded scope (`agents/charter/*` + `agents/_prompt_system/innate-skills/chart/skill.md` + ari/context touchpoints + planning artifacts). Implementation should be a **single developer + reviewer + tester chain** on a shared branch (the commission already names `feature/chart-image-delivery`, head `cf8efbeff9d932a6d01d7cbb2411836057e7b099` — verified in shared meta-kv).

- **Developer** — edits the 9 files (charter workflow/rule/soul/meta, install skill + lib, chart skill, ari workflow, shared context.md). Reuses instance-spawn to verify in the live daemon (the dev lane is already running per the charter workflow.md spot-check).
- **Reviewer** — checks every charter `.md` against `docs/agent-prompt-writing-guide.md` (cardinal/guideline split, one canonical home per artifact, no daemon path leaks). Spot-checks the marker regex against the spec in decisions.md. Verifies the pin-audit `grep -F` passes (NOT a line-number diff — Phase C will insert content above the audit targets).
- **Tester** — runs the existing `test_chart_tools.py` (must stay green) and the new `test_charter_render_capture.py` (18 cases per Components §6). Spot-checks D3 sweep semantics via a unit test on `sweep_once()`. Verifies the 4-signal READINESS_PROBE, the flock-guarded self-heal, and the pre-warm path under simulated cold and warm conditions.

## Tasks (ordered)

| # | Task | Depends on | Acceptance |
|---|------|------------|------------|
| 1 | Write `decisions.md` (D1–D4 verbatim + marker spec + capture contract + restart/promote matrix) | none | File exists at `.agents/shared/planning/chart-image-delivery/decisions.md`; every section cites file:line. |
| 2 | Extend `agents/charter/workflow.md` Step 5 to render PNG + persist + emit marker | #1 | Diff shows the new bash block, the new image_save call, and the new marker-emit line. mktemp hygiene preserved. |
| 3 | Update `agents/charter/rule.md` — add Must for marker contract, Never for "must not strip / must not emit on failure" | #1 | Diff shows +2 lines, no other changes. Verified against `docs/agent-prompt-writing-guide.md` cardinal/guideline split. |
| 4 | Update `agents/charter/soul.md` — add one sentence to "My Principle" | #1 | Diff shows +1 sentence. Style consistent. |
| 5 | Update `agents/charter/meta.json` — `tools.allow += "image"`, `version 1.1.0 → 1.2.0` | #1 | JSON parses; `tools.allow` includes `"image"`; `version` is `1.2.0`. |
| 6 | Create `agents/charter/skills-template/install-mermaid-cli.md` | #1 | File exists; mirrors install-opendesign.md v1.3.0 structure; fences section is verbatim-equivalent; nvm + mmdc + chromium resolution documented; verify + reversal sections present. |
| 7 | Update `agents/_prompt_system/innate-skills/chart/skill.md` — one paragraph on marker preservation | #2 | Diff shows +1 paragraph in the "How to use `generate_chart()`" section. |
| 8 | Charter spawn test — fresh instance, real mmdc, real image_save, real response | #2, #5 | `generate_chart` returns text containing both the mermaid block and the marker; `image_list(feature="chart-render")` finds the row. |
| 9 | Charter spawn test — forced mmdc failure | #2, #5 | `generate_chart` returns text containing the mermaid block, no marker, no exception; `image_list` empty. |
| 10 | Charter spawn test — forced image_save failure (store full) | #2, #5 | `generate_chart` returns mermaid text only, no marker; no exception. |
| 11 | Add `tests/test_charter_render_capture.py` (8 tests) | #2, #5, #6, #7 | New file; all 8 tests green. |
| 12 | Run full `tests/test_chart_tools.py` (regression) | #2, #5, #7 | 100% green; _BUSY_STRING pins intact. |
| 13 | Run full `tests/test_chart_tools_legacy_error_contract.py` (regression) | #2, #5, #7 | 100% green; "Error: ..." return shape intact. |
| 14 | Reviewer pass on every `.md` against `docs/agent-prompt-writing-guide.md` | #3, #4, #6, #7 | Reviewer notes no cardinal/guideline violations, no daemon-path leaks, no `meta.json` references in prose. |
| 15 | Reviewer pass on marker byte-stability | #1, #2, #7 | Reviewer confirms marker regex matches `^<!-- ens-img:chart-render:[a-f0-9]{32} -->$` exactly; one canonical home. |
| 16 | Reviewer pass on restart/promote matrix | #1, #2–#7 | Reviewer confirms Phase A requires NO daemon restart, NO promote. |
| 17 | Create `agents/charter/skills-template/install-mermaid-cli.lib.sh` — extracted READINESS_PROBE library (arch-rec #17) | #1 | File exists, pure bash + jq, exports `charter_readiness_probe`; sourced by workflow.md Step 5. |
| 18 | Add pre-render directive/frontmatter sanitizer to `agents/charter/workflow.md` (arch-rec #15 / F4) | #2 | sed block strips `%%{init}%%` directives and `securityLevel:` frontmatter keys before mmdc invocation. |
| 19 | Wrap render in `( ulimit -v 2097152; timeout 60 … )` and add `-c '{"securityLevel":"strict","htmlLabels":false}'` to mmdc invocation (arch-rec #15 / F4) | #2, #18 | Bash shows the wrapper; mmdc flags include the security pin. |
| 20 | Retire `npx -y @mermaid-js/mermaid-cli` from `workflow.md` (arch-rec #16) — replaced with absolute-path invocation of installed global mmdc | #17 | `grep "npx -y @mermaid-js" agents/charter/workflow.md` returns ZERO matches. |
| 21 | Implement 4-signal READINESS_PROBE in `install-mermaid-cli.lib.sh` + skill `## READINESS_PROBE` section (arch-rec #17) | #17 | Function exported; skill body documents the 4-signal contract; returns 0/1/2. |
| 22 | Implement flock-guarded self-heal in `workflow.md` step 0 + `mermaid-install.lock` + `mermaid-pending-install` async queue marker + inline-install gating (arch-rec #18) | #17, #21 | Bash implements the flock path; lock file is `$HOME/.cache/charter/mermaid-install.lock`; inline-install gated by `cold_misses_in_session < 2 && chromium_partial && 60s_cap`. |
| 23 | R6 ari pre-warm: add one-line reminder to `.agents/shared/context.md` + commissioning-workflow note in `agents/ari/workflow.md` | none | Both files updated; pre-warm responsibility explicitly owned by ari. |
| 24 | Add store-capacity pre-check 🟢 (image_list sum > 80% → skip persist, text-only, `image_store_full` log) per arch-rec Focus 4.6 — OPTIONAL but recommended | #2, #5, #18 | Implementation guarded by the 🟢 toggle; test 17 covers. Phase D will add an audit pin for the pre-warm ownership (per the R6 ratification note). |

## Coupling

- **Tight with Phase B** — Phase B consumes the marker regex from `decisions.md §marker`; if the regex changes, Phase B breaks. Lock the syntax in `decisions.md` first; do not "improve" it during implementation.
- **Tight with Phase C** — Phase C is the parallel worker lane updating agent guidance (any agent that uses `generate_chart` must learn about the marker). Phase A's edit to `agents/_prompt_system/innate-skills/chart/skill.md` (task #7) is the seed; Phase C extends it.
- **Loose with Phase D** — Phase D owns tests + version + docs. Phase A's `test_charter_render_capture.py` (task #11) overlaps with Phase D's hardening scope; coordinate to avoid duplicate coverage.
- **Independent of** — Frontend (no FE change in Phase A), Upgrade pipeline (no daemon change), OpenDesign lane (unrelated), Job queue / message queue (charter is instance-spawned, no job-queue path).

## Dependencies

- **Upstream:** none. (Charter already exists; tmp_images substrate already implemented; image_save already exposes the `image` category.)
- **Downstream:**
  - **Phase B** consumes the marker regex from `decisions.md §marker` and the per-image HTTP route `GET /api/tmp_images/<id>` (already in `daemon/routers/tmp_images.py`).
  - **Phase B** consumes the per-platform size guidance from `decisions.md §capture` (PNG @ 1200w × 2x scale ≈ 50–300 KB; fits Discord 8 MB / Telegram 10 MB / Slack limits comfortably — Phase B's degradation ladder can assume the chart PNG is well under all per-platform limits).
  - **Phase C** consumes the seed paragraph in `agents/_prompt_system/innate-skills/chart/skill.md` (task #7).
  - **Phase D** consolidates the test pack + version matrix.

## Test Strategy

- **Existing tests must stay green:** `tests/test_chart_tools.py` (full), `tests/test_chart_tools_legacy_error_contract.py` (full). **Pin audit (arch-rec Reviewer R9 optional pin-label correction):** `_BUSY_STRING` at chart_tools.py:55 + tests/test_chart_tools.py:296 + skill.md:62 + skill.md:68 (grep -F for the exact string); `_PAUSED_STRING` at chart_tools.py:222 + tests/test_chart_tools.py:298 + skill.md:74 (grep -F for the exact string). All content-addressable greps — NOT line numbers — because Phase C will insert content above the audit targets in the shared `chart/skill.md` file.
- **New unit tests:** 18 cases in `tests/test_charter_render_capture.py` (see Components §6). Adds probe tests (#9-#12), security-pin tests (#13-#15), `npx -y` retirement test (#16), store-capacity pre-check test (#17), and the `grep -F` pin audit test (#18).
- **Charter spawn smoke (manual + CI):** 6 scenarios — happy path, mmdc failure, image_save failure, cold-detect (probe→install→re-probe), pre-warm (ari deploy-step), install-in-progress-other (flock-contended).
- **D3 sweep behavior verification:** confirm `sweep_once()` reaps a 31-day-old `retention_class="normal"` chart PNG; does NOT reap a 31-day-old `retention_class="protected"` entry. (Re-verify, not add a new test — the existing sweep tests cover it.)
- **No FE test, no integration e2e, no Playwright** — Phase A is charter + skill only. (Phase B owns the e2e tests that exercise the astream lane + the full chat dispatch; arch-rec amendment #21 is Phase D's, not Phase A's.)

## Acceptance Criteria (checkboxable)

- [ ] `decisions.md` exists with D1–D4 verbatim, marker spec, capture contract, restart/promote matrix.
- [ ] Every charter validation now renders a PNG, persists it via `image_save`, and emits `<!-- ens-img:chart-render:<image_id> -->` on success.
- [ ] Mermaid text is returned in ALL cases (success, render failure, persist failure, validation skipped) — text delivery NEVER breaks.
- [ ] No marker is emitted when the PNG render OR the image_save call fails (arch-rec #19 / Never rule).
- [ ] `tools.allow` in `agents/charter/meta.json` includes `"image"`; `image-reader` is implicitly a team member.
- [ ] `install-mermaid-cli.md` exists, is idempotent, fenced (no Docker / no apt / no system Node), has HONEST-STOP prereqs (curl / git / jq / `~/.nvm/nvm.sh`), has `## READINESS_PROBE` section, has verify + reversal sections.
- [ ] `install-mermaid-cli.lib.sh` exists (NEW, arch-rec #17), pure bash + jq, exports `charter_readiness_probe` returning 0/1/2.
- [ ] READINESS_PROBE checks 4 signals (arch-rec #17): config file exists+valid JSON; `mmdcPath` absolute+executable; `puppeteerConfig.executablePath` probed at probe time (NOT trusted from config-write time); pinned `mermaidCli` version matches.
- [ ] Hybrid executor (arch-rec #18): ari/commissioner pre-warm is owned (R6 ratified — one-line reminder in `.agents/shared/context.md` + note in `agents/ari/workflow.md`); charter self-heal uses `flock -w 10` on `$HOME/.cache/charter/mermaid-install.lock`; lock-contended → `install_in_progress_other` log + `$HOME/.cache/charter/mermaid-pending-install` async marker + degrade this turn.
- [ ] Inline install gated by `(cold_misses_in_session < 2) && chromium_partial && 60s_cap` (arch-rec #18).
- [ ] `npx -y @mermaid-js/mermaid-cli` is RETIRED from `workflow.md` (arch-rec #16); mmdc invoked by absolute path; worst case `npx --no-install` is permitted.
- [ ] Pre-render sanitizer strips `%%{init}%%` directives and frontmatter `securityLevel:` keys before mmdc invocation (arch-rec #15 / F4).
- [ ] mmdc invocation includes `-c '{"securityLevel":"strict","htmlLabels":false}'` (arch-rec #15 / F4) and is wrapped in `( ulimit -v 2097152; timeout 60 … )` (memory + wall-clock bounds).
- [ ] `rule.md` Must rule (arch-rec #19): emit marker ONLY after a valid `image_save` result. `rule.md` Never rule: do NOT retry render-side failures (syntax-only 3-attempt budget preserved).
- [ ] Optional 🟢 store-capacity pre-check (arch-rec Focus 4.6) implemented + tested.
- [ ] `_BUSY_STRING` and `_PAUSED_STRING` pins unchanged at all 4+ sites (content-addressable `grep -F` per arch-rec Reviewer R9 optional).
- [ ] `tests/test_chart_tools.py` + `tests/test_chart_tools_legacy_error_contract.py` are 100% green.
- [ ] `tests/test_charter_render_capture.py` has 18 green cases.
- [ ] `agents/charter/*` and `agents/_prompt_system/innate-skills/chart/skill.md` conform to `docs/agent-prompt-writing-guide.md` (cardinal/guideline split, one canonical home, no daemon path leaks).
- [ ] No `daemon/`, `tests/` (other than the new test file), or `frontend/` files modified.
- [ ] No daemon restart / promote required.
- [ ] Marker regex is byte-stable: `^<!-- ens-img:chart-render:[a-f0-9]{32} -->$`.

## Risks

| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| 1 | mmdc flaky on warm cache (puppeteer cold start) | Medium | Medium | 60s timeout + 2GB ulimit wrapper; `mktemp` hygiene; degrade-to-text on any non-zero exit. The install skill (D1) pins the chromium path so the cache hit is reliable. |
| 2 | `npx -y @mermaid-js/mermaid-cli` resolution non-deterministic when cache evicted | High | Medium | D1 install skill installs `mmdc` globally under nvm-managed Node and records the absolute path; the render script uses the absolute path, NOT `npx -y` (arch-rec #16 RETIRED). |
| 3 | Non-interactive bash does not source nvm (verified: `~/.bashrc:121-123` interactive-only) — `command -v mmdc` gives PERMANENT false-cold | High | High | D1 install skill ships a `nvm source + resolve` preamble; READINESS_PROBE (arch-rec #17) replaces `command -v` with a 4-signal config-file probe that survives the `$PATH` absence. Render script reads absolute paths from `~/.config/charter-mermaid-puppeteer.json`. |
| 4 | Marker accidentally stripped by parent agent's LLM in a "tidy up" step | Medium | Medium | The chart skill update (task #7) is explicit: "do not strip the trailing `<!-- ens-img:... -->` marker." Phase C extends the same instruction across the agent guidance. |
| 5 | Marker regex drift breaks Phase B | High | Low | Lock the regex in `decisions.md`; reviewer pass (task #15) confirms byte-stability via `grep -F`. |
| 6 | Charter meta.json `tools.allow` change triggers auth-mismatch (e.g. v2 charter variant) | Low | Low | `daemon/tools/_auth.py:35-51` already uses `tools.allow` as the single source of truth; v1 + v2 charter variants read from the same file. |
| 7 | Charter emits the marker WITHOUT a real image_id (LLM hallucination) | Low | Medium | Marker spec is byte-exact: the `image_save` tool returns JSON with `image_id`; charter is instructed (workflow.md) to extract the id from the JSON, not invent one. New test (#5) catches byte-stability. |
| 8 | Image substrate cap (1 GiB default) fills up under chat-attachment load | Low | Low | D3 retention = 30 days, sweep = hourly, cap is generous; new test (#4) covers store-full path. Phase A's chat-attachment load is far below cap. 🟢 Optional pre-check (#17) degrades gracefully if it ever grows. |
| 9 | Charter's existing 3-attempt syntax-retry budget gets confused with new render step | Medium | Medium | Explicit in workflow.md: syntax-retry is preserved for syntax errors only; render-side failures fall through to the degrade branch (arch-rec #19 / Never rule). New tests (#4) cover. |
| 10 | `version` bump in `meta.json` breaks existing pinned-version consumers | Low | Low | `version` is informational in this codebase; no load-bearing code reads it. Version bump to `1.2.0` is semver-appropriate for new capability. |
| 11 | (NEW, arch-rec #15 / F4) `securityLevel` is overridable from inside diagram text (`%%{init}%%` + frontmatter) | High | Medium | Pre-render sed sanitizer strips both; CLI-side `-c '{"securityLevel":"strict","htmlLabels":false}'` pin enforces the override. mmdc 12's `strict` is the maximum. |
| 12 | (NEW, arch-rec #15 / F4) mmdc render can OOM the daemon (puppeteer unbounded) | High | Low | `ulimit -v 2097152` (~2 GB) wraps the render; `timeout 60` wraps the wall-clock. Rasterization-to-PNG neutralizes script/link vectors at the output stage. |
| 13 | (NEW, arch-rec #18) Two concurrent charters race the install | Medium | Medium | `flock -w 10` on `$HOME/.cache/charter/mermaid-install.lock`; loser logs `install_in_progress_other`, writes `$HOME/.cache/charter/mermaid-pending-install`, degrades this turn. Pre-warm path makes this rare in practice. |
| 14 | (NEW, arch-rec #18) Cold chromium download is multi-minute; would block `generate_chart`'s 600s `invoke_and_wait` budget | High | High | Cold-detect mid-turn writes `$HOME/.cache/charter/mermaid-pending-install` and DEGRADES; the install runs async on a subsequent pre-warm or self-heal. The 600s budget is never spent on the install. Inline-install cap is 60s and only for partial chromium + warm pre-warm state. |
| 15 | (NEW, arch-rec #16) `npx -y` stays in workflow.md as residual ad-hoc invocation | High | Low | Hard retirement: test #16 (`grep "npx -y @mermaid-js" workflow.md` returns 0) enforces. Install-skill + lib's absolute-path contract is the substitute. |
| 16 | (NEW) HONEST-STOP prereq missing on a host (e.g. no curl) → charter hangs trying to bootstrap | Medium | Low | Install skill emits structured `HARD_BLOCKER: <missing prereq>` envelope and stops; no retry, no apt-install. Degrade-to-text path covers the chart delivery. |
| 17 | (NEW) Phase C inserts content above the busy/paused string sites in `chart/skill.md`, breaking a line-number-based pin audit | Low | High | All pin assertions migrated to `grep -F` (test #18); reviewer pass (task #14) runs the greps, not a line diff. |

## Open Questions

1. **Should charter emit the marker also when the chart is a `NEEDS MORE INFO` result?** (No — there is no diagram to render. Workflow.md Step 2 short-circuits before Step 5; marker is never emitted. This is implicit but worth stating in the plan review.)
2. **Should charter add a `page` provenance value** (e.g. `page="mermaid-render"`) to distinguish single-chart from batch? Decision: no — `feature="chart-render"` is enough; over-tagging adds noise. Phase B keys on `feature`.
3. **PNG vs SVG for delivery?** The user requirement is "rendered PNG in the channel." SVG is excluded — most chat UIs don't render it as an inline image, and Discord/Slack/Telegram attachment APIs prefer PNG/JPG. SVG stays internal as a validation artifact.
4. **Should the install skill also be added to the `worker` agent's `skills-template/`** so non-charter agents (e.g. designer) can leverage it? Out of scope for Phase A. Phase C/D decision.
5. **arch-rec §7 unverified — `TmpImageStoreFull` cap configurability:** 1 GiB default; not pinned in `decisions.md` §D3. Affects the optional capacity pre-check only. **Verify at impl:** is there a per-project cap override in `ServicesConfig`? If yes, expose the configured value to the pre-check (test #17). If no, the 1 GiB constant is the floor.
6. **arch-rec §7 unverified — Telegram limiter actual rate:** explorer cited 30 msg/s token bucket; leader context cited 30 msg/30s. **Does not change Phase A ownership**; Phase B verifies at implementation.
7. **arch-rec §7 unverified — inline-install 60s cap is heuristic.** Log actual install time on first success; write `pinned.observedInstallSec` to `~/.config/charter-mermaid-puppeteer.json`; re-tune the cap from the observed value. (Plan §4 step 6 captures this.)
8. **arch-rec §7 unverified — mmdc 12 render vs frontend mermaid (ngx-markdown) version drift.** Cosmetic parity only. Frontend is Phase D's territory.

## Restart / Promote Matrix (Phase A)

| File | Requires daemon restart? | Requires promote? |
|------|---------------------------|-------------------|
| `agents/charter/workflow.md` | NO (picked up at next charter spawn) | NO |
| `agents/charter/rule.md` | NO | NO |
| `agents/charter/soul.md` | NO | NO |
| `agents/charter/meta.json` | NO (picked up at next spawn) | NO |
| `agents/charter/skills-template/install-mermaid-cli.md` | NO (skill_seed_service consumes on next spawn) | NO |
| `agents/charter/skills-template/install-mermaid-cli.lib.sh` | NO (sourced on every render; bash is interpreted) | NO |
| `agents/_prompt_system/innate-skills/chart/skill.md` | NO (innate skill loaded on next instance spawn) | NO |
| `.agents/shared/context.md` | NO (planning artifact) | NO |
| `agents/ari/workflow.md` | NO (next ari spawn reads the updated workflow) | NO |
| `.agents/shared/planning/chart-image-delivery/decisions.md` | n/a — planning artifact | n/a |
| **Phase A as a whole** | **NO** | **NO** |

This is a planning artifact and an agent-prompt change only. No `daemon/` code, no migration, no restart, no promote. Phase B (the dispatcher + adapter changes) is the one that requires a promote.

> **R6 ratification note (R2 fold-in):** the ari pre-warm responsibility is OUT-OF-SCOPE for this commission's original file list but is ratified as in-scope for Phase A because the architecture review (amendment #18) identified it as the only durable solution to the cold-install-blocking-600s-budget problem. Phase D will add an audit pin for the pre-warm ownership; the pin will reference the one-line reminder in `.agents/shared/context.md` and the commissioning note in `agents/ari/workflow.md`.
