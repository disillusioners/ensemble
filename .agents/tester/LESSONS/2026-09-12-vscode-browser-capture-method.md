# VS Code Web-Workbench Browser Capture — Method Traps (2026-09-12)

Source: image-preview evidence-capture run (see RESULTS/2026-09-12-vscode-image-preview-browser-capture.md). Traps below cost ~4-8 min across workers; all are repeatable hazards for the upcoming fix-verification run.

## 1. Gitignored image files are invisible to Quick Open
- Deterministic `find` ignores `.gitignore` — it returned `.inspiration-projects/DeepCode/assets/Deepcode.png` (gitignored via `.gitignore:65`), but Quick Open (Control+P) hides ignored files → dead end that looks like "file missing".
- **Rule:** `git check-ignore <path>` BEFORE relying on Quick Open; otherwise open via **Explorer tree click** (shows ignored files, dimmed). Never edit daemon user-data settings as a workaround during evidence runs.

## 2. Headless VS Code web workbench is iteration-heavy
- Expect 60-90s for large-folder scan before the Explorer tree is usable; chevron-click expansion needed; DOM probe selectors need refinement (8 iterations on the capture run).
- Working recipe is now a durable skill: `playwright-code-server-webbench-capture` (38919a09); harness script preserved at `RESULTS/assets/2026-09-12-vscode-capture/capture.py`.

## 3. Worker shells inherit daemon env — PORT overrides --bind-addr
- code-server prioritizes the `PORT` env var over `--bind-addr` → first standalone launch died EADDRINUSE on the inherited 9797.
- **Rule:** pin `PORT=<intended>` explicitly for every manually launched code-server.

## 4. Render verdicts: probe, don't screenshot
- First "RENDERED" verdict in the side-by-side run was a Welcome-tab logo false positive.
- **Rule:** assert render via in-iframe `img.naturalWidth > 0` probe + byte-fetch 200 + dialog-absence; screenshot is supporting evidence only.

## 5. Static curl analysis ≠ browser truth
- The dispatched hypothesis (loopback-host image URL → 404) was flat disproven by browser capture: real failure is meta-CSP blocking the media-preview extension's own assets (request never leaves the browser; no HTTP status exists).
- **Rule:** capture real browser network events BEFORE designing any fix around a statically-inferred request shape.

## 6. Process-tree assumptions age fast
- The "orphaned tree" premise was wrong twice over: the target tree was parented by a live prod daemon, and true strays (PPID=1 extension hosts) never carry the root's flags/checkout path — child-process kill criteria must be calibrated per process TYPE, not per tree.
- Killing strays triggers the live code-server to respawn extension hosts within 1-2s — expect new PIDs post-kill; they are healthy tree children, not new orphans.

## 7. Cleanup ordering: kill the daemon BEFORE its code-server (respawn race)
- Killing an embedded code-server root while its daemon parent still lives opens a window in which the daemon's vscode manager auto-respawns a replacement; when the daemon then dies, the replacement reparents to launchd and becomes YOUR orphan (observed 2026-09-12: PIDs 88674/88676 created exactly this way during capture cleanup).
- **Rule:** stop the daemon (manager) FIRST, then kill the code-server tree it spawned; verify by post-cleanup sweep that no `--bind-addr 127.0.0.1:0 --auth none` process with YOUR user-data-dir signature survives.

## 8. Deterministic-find pollution + single-variable discipline
- Evidence artifacts written under `.agents/tester/RESULTS/assets/` (PNGs) POLLUTE the deterministic first-image `find | sort | head -1` rule — later runs silently target a different image. Add `-not -path '*/.agents/*'` to the rule, or pin the target by absolute path.
- The side-by-side run (G3) changed two variables at once (version + access path) and produced a wrong "version-specific" conclusion; the decisive run (version swapped, proxy held constant) reversed it. **Rule: one variable per discriminator run — enumerate the variables you're changing before claiming isolation.**

## 9. Depth-N iframe recursion + uvicorn --reload SIGTERM absorption (2026-09-12 merge gate)
- VS Code web workbench nests webviews INSIDE webviews — the rendered `<img>` sat at **depth 2**; a depth-1 probe reported imgCount=0 while the image actually rendered. **Rule:** render probes must recurse (`probeDoc(inner, depth+1, maxDepth=6)`).
- `uvicorn --reload` silently absorbs SIGTERM — a TERM'd master survives. **Rule:** daemon cleanup = SIGKILL the uvicorn master first (trap #7 order unchanged), then surviving code-server PIDs.

## 10. Log discipline (known, re-confirmed)
- `data/logs/ensemble.log` is append-only and NOT line-chronological — baseline `wc -l` pre-boot, grep only post-baseline, time-bracket by timestamps.
