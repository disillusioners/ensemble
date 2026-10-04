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

- **NO Docker, NO `apt`/system packages, NO system Node touch**
- **NO remote fetch-and-execute** — no auto-installing package-runner
  invocations (the `npx` fetch-the-package-and-run pattern), no
  curl-piped tool CLIs at render or install time. Every CLI comes from
  `npm i -g <pkg>@<pinned-major>` under the nvm-managed Node and is
  invoked by ABSOLUTE path. `npx --no-install` is the sanctioned
  fallback idiom: it resolves an already-installed copy or refuses
  loudly — it never fetches.
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
- **NO remote fetch-and-execute.** No auto-installing package-runner
  invocations — no unpinned, fetch-and-run CLI calls, ever
  (tsc-typosquat hygiene). Every CLI arrives via
  `npm i -g <pkg>@<pinned-major>` under the nvm Node and runs by
  absolute path; `npx --no-install` is the only sanctioned npx form
  (resolves an installed copy or refuses loudly).
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

### Step 0 — Source the lib + warm-probe fast path (idempotency gate)

```bash
# 0. Source the extracted library (same repo-root-relative form as
#    workflow.md Step 5.0), then take the idempotent fast path: a warm
#    probe means the toolchain is already provisioned — verify-only,
#    install nothing.
. agents/charter/skills-template/install-mermaid-cli.lib.sh

if charter_readiness_probe; then
    echo "probe warm — verify-only fast path, no install"
    if charter_verify_toolchain "$MMDC_BIN" "$CHARTER_PUPPETEER_CONFIG"; then
        charter_clear_pending_install_marker 2>/dev/null || true
        charter_release_install_lock 2>/dev/null || true
        echo "install-mermaid-cli: OK (verify-only fast path — already warm)"
        exit 0
    fi
    # Verify FAILED on a supposedly-warm config — the recorded paths are
    # stale or broken. Remove the config so the probe goes cold again,
    # then fall through to the full install below. This is the failure
    # half of the idempotency contract: a stale warm config must never
    # keep passing the fast path.
    echo "verify FAILED on warm config — removing stale config, full install follows"
    rm -f "$CHARTER_PUPPETEER_CONFIG"
fi
```

A non-warm probe (rc=1 cold, or rc=2 lock held) falls through to the
full install below. There is no inline-install variant: the install
either runs as THIS skill (behind the self-heal lock taken by
workflow.md's cold branch) or it does not run at all.

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
# Final fallback: scan the nvm bin dirs (never `which mmdc` — $PATH can
# capture a non-nvm binary and pin the wrong toolchain)
[ -x "$MMDC_PATH" ] || MMDC_PATH="$(find "$NVM_DIR/versions/node" -name mmdc -type f 2>/dev/null | head -1)"
[ -x "$MMDC_PATH" ] || { echo "HARD_BLOCKER: mmdc not found after install"; charter_release_install_lock 2>/dev/null || true; exit 1; }
```

### Step 3 — Chromium resolution (puppeteer download)

mmdc invokes puppeteer at render time, which spawns chromium from
`~/.cache/puppeteer/chrome/`. The install must ensure chromium is
present (puppeteer's `npm i` does NOT auto-download; the install
script must trigger the download explicitly).

```bash
# 5. Install the browser-fetch CLI under the nvm-managed Node —
#    PINNED major (same idiom as mermaid-cli@12), invoked by absolute
#    path. NEVER remote fetch-and-execute (see Fences: the npx
#    auto-install pattern is banned; `npx --no-install` is the only
#    sanctioned form — installed copies or loud refusal, never a
#    fetch).
npm i -g @puppeteer/browsers@3

# 6. Capture the absolute bin path (same idiom as mmdc above)
BROWSERS_BIN="$(nvm which 24 | xargs dirname)/browsers"
[ -x "$BROWSERS_BIN" ] || BROWSERS_BIN="$(find "$NVM_DIR/versions/node" -path '*@puppeteer/browsers/lib/main-cli.js' -type f 2>/dev/null | head -1)"
[ -x "$BROWSERS_BIN" ] || { echo "HARD_BLOCKER: browsers CLI not found after install"; charter_release_install_lock 2>/dev/null || true; exit 1; }

# 7. Download chromium — the build is PINNED BY MAJOR. `chrome@stable`
#    floats: every fresh install could silently swap chromium builds.
#    A major pin resolves (via the CLI's build resolution) to the
#    newest build OF THAT MAJOR and stays there until the pin is
#    bumped deliberately. The exact binary path is recorded in the
#    staged config (Step 4) and re-probed at probe time by the
#    READINESS_PROBE.
CHROME_MAJOR_PIN="${CHROME_MAJOR_PIN:-154}"
PUPPETEER_CACHE_DIR="$HOME/.cache/puppeteer"
"$BROWSERS_BIN" install "chrome@$CHROME_MAJOR_PIN" --path "$PUPPETEER_CACHE_DIR"

# 8. Locate the chromium binary
CHROME_PATH="$(find "$PUPPETEER_CACHE_DIR" -name 'chrome' -type f 2>/dev/null | head -1)"
[ -x "$CHROME_PATH" ] || { echo "HARD_BLOCKER: chromium not found after download"; charter_release_install_lock 2>/dev/null || true; exit 1; }
```

### Step 4 — STAGE the install config (NOT the probe path yet)

```bash
# 9. STAGE the config to a temp path. The probe-visible config at
#    ~/.config/charter-mermaid-puppeteer.json is written ONLY after
#    verify passes (Step 5 → Step 6 promotion). Ordering matters: a
#    config written before verify would let a broken partial install
#    satisfy the probe's static checks forever — false-warm rc 0,
#    cold-detect never re-fires, silent text-only degradation.
#    Sandbox policy (arch-rec §3 amendment #15): args start EMPTY —
#    the sandboxed launch is the default; --no-sandbox is applied only
#    as the logged single fallback inside the verify/render contract.
TMPCFG_STAGE="$(mktemp "${TMPDIR:-/tmp}/charter-mermaid-puppeteer.XXXXXX.json")"
trap 'rm -f "$TMPCFG_STAGE"' EXIT
mkdir -p "$HOME/.config"
cat > "$TMPCFG_STAGE" <<EOF
{
  "mermaidCli": 12,
  "mmdcPath": "$MMDC_PATH",
  "puppeteerConfig": {
    "executablePath": "$CHROME_PATH",
    "args": []
  }
}
EOF
```

The 4-signal READINESS_PROBE in `install-mermaid-cli.lib.sh` reads
the PROMOTED config (Step 6). On signal 3 (chromium path re-probed at
probe time), cache eviction is survived.

### Step 5 — Verify (evidence not claims — gates promotion)

```bash
# 10. Verify against the STAGED config. charter_verify_toolchain runs
#     the exact render contract (sanitized input, ulimit -v 2 GB,
#     timeout 60, security pin, sandboxed-first launch with one logged
#     --no-sandbox fallback) and requires real evidence: non-empty SVG
#     AND PNG, and file(1) reporting PNG image data.
#
#     ANY verify failure exits non-zero WITHOUT promoting the staged
#     config — the probe stays cold and the next render re-fires this
#     install. The EXIT trap removes the staged file.
charter_verify_toolchain "$MMDC_PATH" "$TMPCFG_STAGE" || {
    echo "VERIFY FAILED — staged config discarded, probe stays cold (no false-warm)"
    charter_release_install_lock 2>/dev/null || true
    exit 1
}
```

### Step 6 — Promote + record + release (only after verify passes)

```bash
# 11. VERIFY PASSED — NOW promote the staged config to the probe path.
mv "$TMPCFG_STAGE" "$HOME/.config/charter-mermaid-puppeteer.json"
trap - EXIT   # staged file no longer exists; nothing left to clean

# 12. Capture observed install time → config (inline-install cap heuristic)
INSTALL_SECS=$SECONDS
TMP_CFG="$HOME/.config/charter-mermaid-puppeteer.json"
if [ -f "$TMP_CFG" ] && command -v jq >/dev/null 2>&1; then
    TMP_CFG_NEW=$(mktemp)
    jq --argjson s "$INSTALL_SECS" '. + {pinned: {observedInstallSec: $s}}' \
        "$TMP_CFG" > "$TMP_CFG_NEW" && mv "$TMP_CFG_NEW" "$TMP_CFG"
fi

# 13. Clear the async queue markers (the warm install supersedes the
#     queue), reset the self-heal attempt counter, and release the lock
#     acquired by workflow.md's cold branch.
charter_clear_pending_install_marker 2>/dev/null || true
charter_clear_install_session_state 2>/dev/null || true
charter_release_install_lock 2>/dev/null || true

# Report
echo "install-mermaid-cli: OK (mmdc=$MMDC_PATH, chromium=$CHROME_PATH, observedInstallSec=$INSTALL_SECS)"
```

### Failure path (lock release is mandatory)

On ANY failure exit — `HARD_BLOCKER` prereq, install error, verify
failure — the one-liner `charter_release_install_lock` MUST run before
stopping (every failing branch above already carries it). The session
attempt counter is deliberately NOT reset on failure: it is what caps
self-heal at 2 attempts per session so a host that can never succeed
stops flailing. The lock release is what lets the NEXT session (or the
operator, after fixing the blocker) retry cleanly.

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

## READINESS_PROBE (single source of truth — see install-mermaid-cli.lib.sh)

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
chromium on first install). The hybrid executor's lock-guarded
self-heal + async queue marker is the structural answer — the install
runs in the background or on the next pre-warm, NEVER inside
`generate_chart`'s 600s `invoke_and_wait` budget. The user's first
chart on a cold host returns text-only Mermaid immediately; the
next render (likely minutes later) is warm.

### Self-heal cap and lock lifecycle (amendment #18, as wired)

- **Attempt cap.** Self-heal runs at most TWICE per session. The
  `cold_misses_in_session` counter (`~/.cache/charter/mermaid-session-state.json`)
  is bumped on every cold-detect self-heal and cleared on a successful
  install (Step 6). Beyond the cap the cold path stops attempting:
  async queue marker + degrade this turn. This is what stops a host
  with a permanent blocker (no curl, no git) from flailing the install
  skill on every cold render.
- **Lock held ACROSS the install.** workflow.md's cold branch acquires
  `~/.cache/charter/mermaid-install.lock` and this skill releases it —
  on success (Step 6) and on every failure exit. While the lock is
  fresh, concurrent charters' probes return rc=2 and degrade: no
  concurrent `npm i -g`, no interleaved downloads.
- **Staleness.** A lock older than `CHARTER_LOCK_STALE_SECS`
  (default 1800s — must exceed the worst-case cold-install duration)
  is treated as abandoned: the probe removes it and proceeds. A
  crashed install therefore cannot wedge the host into permanent
  rc=2 degrades.

There is deliberately NO inline-install variant (no "install inside
the render bash block"): the install either runs as this skill behind
the lock, or it does not run. The verify-gated promotion (Step 5 →
Step 6) plus the fast path (Step 0) are the complete idempotency
contract.
