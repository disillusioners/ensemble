---
version: 1.0.0
category: execution
auto_load: false
---

# install-mermaid-cli — provision the mermaid-cli + puppeteer + chromium toolchain

You are provisioning the LOCAL headless render toolchain that
charter uses to convert validated Mermaid to a PNG. The render
chain is **mmdc** (mermaid-cli v12) → **puppeteer** (Node-side
chromium driver) → **chromium** (~/.cache/puppeteer/chrome/). All
three are user-space — no Docker, no apt, no system Node touch.

The install procedure is **fenced** (mirrors the install-opendesign
pattern; same v1.3.0 lineage, job f9946b1d):

- NO Docker, NO `apt`/system packages, NO system Node touch
- HONEST-STOP on hard blockers — if a prereq is missing,
  emit `HARD_BLOCKER: <prereq>` and STOP. Never apt-install.
- Idempotent — re-running on a warm host is a verify-only fast path
- Pure bash + jq, fits Phase A's agent-prompt-only fence

## Fences (preserved verbatim from install-opendesign v1.3.0 lineage)

- **NO Docker** on the install host (no Docker, no Docker Compose,
  no Docker socket). The mermaid-cli install is user-space only.
- **NO apt / system packages**. No `sudo apt-get install`. No
  system Node touch. Every native dep resolves via prebuilt binaries
  (chromium via puppeteer's download). If a future dep forces source
  compile without `make`, STOP and report; do NOT apt-install.
- **No plaintext secrets anywhere** — files, reports, command echoes,
  tool-results, checkpoints. mermaid-cli has no secrets; the install
  reads / writes no credential material.
- **No env-var value in any report** — key NAMES only. The skill body,
  the `Result:` envelope, and the install report carry only what
  needs to be public (paths, versions).
- **HONEST-STOP on hard blockers** — if a precondition is missing
  (curl / git / jq / `~/.nvm/nvm.sh`), the skill emits the envelope
  and STOPS. No flailing, no retries, no fallback to apt-install.
- **Idempotent** — re-running on a fully-working host is a verify-only
  fast path; no install call, no row write.

## Pre-flight detect (mandatory first step)

```bash
# Prereq check — every missing one is a HARD_BLOCKER, never auto-install
command -v curl    # nvm bootstrap
command -v git     # chromium download (puppeteer)
command -v jq      # READINESS_PROBE (consumed by install-mermaid-cli.lib.sh)
[ -s "$HOME/.nvm/nvm.sh" ]   # Node version detection (nvm user-space)
```

If any is missing, emit the structured envelope and stop:

```
HARD_BLOCKER: <missing prereq>
```

No retries, no apt-install, no fallback. The chart PNG is layered
enhancement — text-only Mermaid delivery is the universal floor
(rule.md Must rule). Missing prereqs degrade to text-only without
blocking the user's request.

## Resolve the toolchain (steps)

### Step 1 — nvm bootstrap (user-space, no system Node touch)

```bash
# 1. Install nvm to user space (idempotent; ~/.nvm already present
#    is a verify-only fast path)
[ -s "$HOME/.nvm/nvm.sh" ] || {
    curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.8/install.sh | bash
}
export NVM_DIR="$HOME/.nvm"; . "$NVM_DIR/nvm.sh"

# 2. Install Node 24 under nvm (NOT system Node)
nvm install 24
NVM_NODE="$(nvm which 24)"   # absolute path capture
```

The `nvm which 24` idiom is the canonical one (per install-opendesign
precedent). The probe in `install-mermaid-cli.lib.sh` accepts EITHER
this OR `nvm-exec` — both resolve to the same absolute Node path.

### Step 2 — Global mmdc install (under nvm-managed Node 24)

```bash
# 3. Install mermaid-cli@12 globally under the nvm-managed Node
npm i -g @mermaid-js/mermaid-cli@12

# 4. Capture the absolute path — non-interactive bash does NOT source
#    ~/.bashrc (interactive-only), so `command -v mmdc` is unreliable.
MMDC_PATH="$(nvm which 24 | xargs dirname)/mmdc"
[ -x "$MMDC_PATH" ] || MMDC_PATH="$(which mmdc 2>/dev/null || true)"
# Final fallback: scan the nvm bin dir
[ -x "$MMDC_PATH" ] || MMDC_PATH="$(find "$NVM_DIR/versions/node" -name mmdc -type f 2>/dev/null | head -1)"
[ -x "$MMDC_PATH" ] || { echo "HARD_BLOCKER: mmdc not found after install"; exit 1; }
```

### Step 3 — Chromium resolution (puppeteer download)

mmdc invokes puppeteer at render time, which spawns chromium from
`~/.cache/puppeteer/chrome/`. The install must ensure chromium is
present (puppeteer's `npm i` does NOT auto-download; the install
script must trigger the download explicitly).

```bash
# 5. Trigger puppeteer's chromium download via the install script
PUPPETEER_CACHE_DIR="$HOME/.cache/puppeteer"
npx -y @puppeteer/browsers install chrome@stable \
    --path "$PUPPETEER_CACHE_DIR" 2>&1 | tail -5

# Alternative (newer puppeteer): install via puppeteer's CLI
# npx puppeteer browsers install chrome --path "$PUPPETEER_CACHE_DIR"

# 6. Locate the chromium binary
CHROME_PATH="$(find "$PUPPETEER_CACHE_DIR" -name 'chrome' -type f 2>/dev/null | head -1)"
[ -x "$CHROME_PATH" ] || { echo "HARD_BLOCKER: chromium not found after download"; exit 1; }
```

### Step 4 — Write the install config file (read by READINESS_PROBE)

```bash
# 7. Write the config file the render script reads
mkdir -p "$HOME/.config"
cat > "$HOME/.config/charter-mermaid-puppeteer.json" <<EOF
{
  "mermaidCli": 12,
  "mmdcPath": "$MMDC_PATH",
  "puppeteerConfig": {
    "executablePath": "$CHROME_PATH",
    "args": ["--no-sandbox"]
  }
}
EOF
```

The 4-signal READINESS_PROBE in `install-mermaid-cli.lib.sh` reads
this file. On signal 3 (chromium path re-probed at probe time),
cache eviction is survived.

### Step 5 — Verify (evidence not claims)

```bash
# 1. mmdc --version reports 12.x
"$MMDC_PATH" --version  # expect: 12.0.0

# 2. Minimal diagram renders to BOTH out.svg and out.png
TMPDIR_TEST=$(mktemp -d)
cat > "$TMPDIR_TEST/test.mmd" <<'EOF'
flowchart TD
    A-->B
EOF
"$MMDC_PATH" -i "$TMPDIR_TEST/test.mmd" -o "$TMPDIR_TEST/out.svg" \
    -t default -b white -w 1200 -s 2 \
    -c '{"securityLevel":"strict","htmlLabels":false}' \
    --puppeteerConfigFile "$HOME/.config/charter-mermaid-puppeteer.json" \
    --quiet
[ -s "$TMPDIR_TEST/out.svg" ] || { echo "VERIFY FAILED: out.svg empty"; exit 1; }
"$MMDC_PATH" -i "$TMPDIR_TEST/test.mmd" -o "$TMPDIR_TEST/out.png" \
    -t default -b white -w 1200 -s 2 \
    -c '{"securityLevel":"strict","htmlLabels":false}' \
    --puppeteerConfigFile "$HOME/.config/charter-mermaid-puppeteer.json" \
    --quiet
[ -s "$TMPDIR_TEST/out.png" ] || { echo "VERIFY FAILED: out.png empty"; exit 1; }

# 3. file(1) reports PNG image data
file "$TMPDIR_TEST/out.png" | grep -q "PNG image data" \
    || { echo "VERIFY FAILED: out.png not PNG"; exit 1; }

# 4. Capture observed install time (used by inline-install 60s cap heuristic)
SECONDS_ELAPSED=$SECONDS
charter_bump_install_session_state 2>/dev/null || true   # nudge
rm -rf "$TMPDIR_TEST"
```

### Step 6 — Record + report

```bash
# Capture observed install time → config (for inline-install cap heuristic)
INSTALL_SECS=$SECONDS
TMP_CFG="$HOME/.config/charter-mermaid-puppeteer.json"
if [ -f "$TMP_CFG" ] && command -v jq >/dev/null 2>&1; then
    TMP_CFG_NEW=$(mktemp)
    jq --argjson s "$INSTALL_SECS" '. + {pinned: {observedInstallSec: $s}}' \
        "$TMP_CFG" > "$TMP_CFG_NEW" && mv "$TMP_CFG_NEW" "$TMP_CFG"
fi

# Clear any pending-install marker (the warm install supersedes the queue)
charter_clear_pending_install_marker 2>/dev/null || true
charter_clear_install_session_state 2>/dev/null || true

# Report
echo "install-mermaid-cli: OK (mmdc=$MMDC_PATH, chromium=$CHROME_PATH, observedInstallSec=$INSTALL_SECS)"
```

## Reversal

```bash
# 1. Remove the global mmdc
npm rm -g @mermaid-js/mermaid-cli 2>/dev/null || true

# 2. Remove the nvm-managed Node 24 (charter-owned; safe to clear)
nvm uninstall 24 2>/dev/null || true

# 3. Remove the puppeteer chromium cache (~200 MB)
rm -rf "$HOME/.cache/puppeteer"

# 4. Remove the install config + cache state
rm -f "$HOME/.config/charter-mermaid-puppeteer.json"
rm -f "$HOME/.cache/charter/mermaid-pending-install"
rm -f "$HOME/.cache/charter/mermaid-session-state.json"
rm -f "$HOME/.cache/charter/mermaid-install.lock"
```

## ## READINESS_PROBE (single source of truth — see install-mermaid-cli.lib.sh)

The 4-signal READINESS_PROBE lives in the extracted library
`install-mermaid-cli.lib.sh` (sourced by charter's workflow.md Step 5).
The skill body and the lib are kept byte-synchronized — when this
section changes, the lib's `charter_readiness_probe` function must
change to match.

### Contract

| Signal | Source | Why this is needed |
|--------|--------|---------------------|
| 1. `~/.config/charter-mermaid-puppeteer.json` exists + is valid JSON | the install's own config file | `command -v mmdc` consults `$PATH`, which non-interactive bash does NOT populate with nvm paths |
| 2. `mmdcPath` is absolute + executable | jq + `[ -x ]` | nvm shim lives at `~/.nvm/versions/.../bin/`, NOT in daemon-spawned `$PATH` |
| 3. `puppeteerConfig.executablePath` is absolute + executable, re-probed at probe time | `[ -x ]` at probe time | chromium may have moved (cache evict, manual edit, version upgrade); probing on every render survives moves |
| 4. `mermaidCli` major == 12 | jq equality | mmdc v11 changed the `-c` syntax; mmdc v13 may change again. The major is the contract. |

### Exit codes

- `0` — warm: `MMDC_BIN` and `PUPPETEER_EXECUTABLE_PATH` exported,
  ready to render
- `1` — cold: any signal failed; charter invokes this install skill
  behind an advisory lock
- `2` — install-in-progress-other: another charter holds the
  install lock; charter writes the async queue marker
  (`~/.cache/charter/mermaid-pending-install`) and degrades this turn

### Hybrid executor (deployed at install time)

The probe result drives the charter's render path:

1. `rc=0` warm → render proceeds
2. `rc=1` cold → charter invokes the install skill (this file)
3. `rc=2` install-in-progress-other → log + async queue marker +
   degrade this turn (text-only Mermaid, no marker)

Cold chromium download is multi-minute (puppeteer downloads ~150 MB
chromium on first install). The hybrid executor's flock-guarded
self-heal + async queue marker is the structural answer — the install
runs in the background or on the next pre-warm, NEVER inside
`generate_chart`'s 600s `invoke_and_wait` budget. The user's first
chart on a cold host returns text-only Mermaid immediately; the
next render (likely minutes later) is warm.

Inline install is gated by ALL three of: `cold_misses_in_session < 2`
(state held in `~/.cache/charter/mermaid-session-state.json`) AND
chromium partial present (resumable download) AND hard 60s cap
(heuristic; tunes from `pinned.observedInstallSec` on first success).
