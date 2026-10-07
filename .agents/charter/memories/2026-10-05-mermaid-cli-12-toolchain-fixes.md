# mermaid-cli 12.0.0 toolchain: six latent lib defects fixed on first real bootstrap (2026-10-05)

Host: nea@ensemble-src (dev repo). `/bin/sh` is DASH (bash tool runs dash — `$SECONDS` and
`${var:0:1}` bashisms fatal under `set -u`; run scripts via `bash file.sh`). Container-grade:
chromium sandbox cannot engage ("No usable sandbox!" — Ubuntu 23.10+ AppArmor userns
restriction); the sanctioned --no-sandbox fallback is REQUIRED for every render.

Registry: latest @mermaid-js/mermaid-cli@12 is 12.0.0 (12.x latest). chromium 154 was already
cached at ~/.cache/puppeteer (mtime ~6.5d old) — bootstrap did NOT download chromium.

Fixes applied to agents/charter/skills-template/install-mermaid-cli.lib.sh (+ .md prose sync),
UNCOMMITTED on latest @52ab3b6e — needs a proper worktree-branch merge:
1. `-w 1200` -> `--size 1200` (12.x removed -w/--width).
2. `-c '<inline JSON>'` -> staged temp-file `-c "$mmd_json"` (12.x -c takes a FILE path only).
3. Dropped `ulimit -v 2097152` from both render invocations — chromium 154 cannot launch
   under ANY practical VA cap (fails even 8 GB, and the failure masks the sandbox signature
   so the --no-sandbox fallback never fires). timeout 60 stays the wall-clock bound.
4. `_charter_mmdc_render` prepends `dirname $mmdc_bin` to PATH — the mmdc shim shebang is
   `#!/usr/bin/env node`; without it a system node (22.x) runs the nvm-24 toolchain (WASM
   init OOM / wrong runtime).
5. `charter_verify_toolchain` now derives a TOP-LEVEL puppeteer cfg
   ({executablePath, args}) from the staged 4-signal config before rendering — passing the
   staged file directly (nested .puppeteerConfig) silently drops executablePath AND args.
6. The --no-sandbox fallback jq now overrides top-level `.args` (was `.puppeteerConfig.args`
   — never reached puppeteer). Also fixed mmd_json temp leak on the success-return path.

Bootstrap wall-clock (first render): ~18 min total (probe->warm), dominated by diagnosing
the six defects; mechanics were fast (npm re-installs seconds, verify pass 10s, render 5s,
chromium pre-cached). Verify+promote succeeded; probe now warm; session-state cleared.

TRANSPORT LIMITATION (open): image_save content_b64 must pass through model output; a 56 KB
PNG (75.7 KB base64) degenerated into repetition-garbage during emission — the call failed
(missing content_type saved us from persisting corrupt bytes; sha256 ground truth was on
hand to detect it). chart-render PNGs of this size CANNOT be byte-exactly transported by
model-echo in this configuration. Degrade path taken: text-only Mermaid, no ens-img marker.
Candidate future fixes: small-PNG lane (<= ~20 KB), or a daemon-side path/file bridge for
image_save, or image_store ingest from a local path.
