# VS Code Image-Preview Failure — Browser-Side Evidence Capture (Verification Harness Build)

- **Date:** 2026-09-12
- **Workers:** A `orphan-hygiene` 4a4a0fc5 (infra, no skill) · C `sbs-code-server` cb50451c (`e2e-test`) · B `vscode-browser-capture` a77ba1fb (`e2e-test`)
- **Plan ref:** Blueprint *Editor / Code-Server Integration (Daemon)*
- **Mission:** capture the ACTUAL failing browser request for embedded code-server image preview; close G1 (host proof), G2 (query shape), G3 (version discriminator). Read-only run — **no repo changes, no commits**.

## Verdict (one line)

**Two hypotheses disproven across three runs; final root cause: the daemon's proxy path.** (1) The original static loopback-404 hypothesis was disproven by the first browser capture — the failing requests are **meta-CSP blocks** on the `vscode-resource.vscode-cdn.net` virtual host (path-only URLs, no query), so the media-preview extension never executes and the image is never fetched. (2) The "version-specific / upgrade fixes it" implication of the direct side-by-side (4.137.0 renders standalone) was disproven by the decisive proxy run — **4.137.0 through `/vscode` fails with the IDENTICAL CSP block**. Evidence matrix: 4.112.0+proxy BLOCKED · 4.137.0+proxy BLOCKED (identical) · 4.137.0+direct RENDERS → the differentiator is the **proxy path**, not the version. Fix must be daemon-side (proxy CSP/SW/URL wiring).

## G1 — Failing request host: **CLOSED (hypothesis disproven)**

Captured in real Chromium (Playwright headless, dev daemon on 8079, own code-server P_cs=62682), phase FILE_OPEN/FIRST_CLICK:

| # | Method | URL (exact, as captured) | Failure |
|---|--------|--------------------------|---------|
| 1 | GET | `https://vscode-remote+localhost-003a8079.vscode-resource.vscode-cdn.net/opt/homebrew/Cellar/code-server/4.112.0_1/libexec/lib/vscode/extensions/media-preview/media/imagePreview.css` | **csp** (browser-level block; no HTTP status — request never left the browser) |
| 2 | GET | `https://vscode-remote+localhost-003a8079.vscode-resource.vscode-cdn.net/…/media-preview/media/imagePreview.js` | **csp** |

- `003a8079` is the dotted-hex encoding of `:8079` — the virtual host rewrites to `http://localhost:8079/vscode-remote-resource` territory. **NOT** `127.0.0.1:62682` (P_cs was never contacted by the failing fetches) and **NOT** a 404.
- DOM evidence: tab `"Deepcode.png, preview"` active; 1 webview (`extensionId=vscode.media-preview`); **rendered images: 0**; the "error occurred while loading the image" dialog text **absent from DOM** — the dialog is rendered by the very JS that CSP blocks.

## G2 — Served-endpoint query shape: **CLOSED**

- **Failing URLs: path-only.** No `?path=`, no `?url=`, no `tkn=` at all.
- **Working contrast (same session, same browser):** built-in extension resources fetched via
  `/vscode/stable-<hash>/vscode-remote-resource?path=<urlencoded-abs-path>&tkn=` → **200** (theme JSONs, icon fonts).
  → The daemon's `/vscode` proxy and the `vscode-remote-resource?path&tkn` endpoint are **PROVEN GOOD**; the media-preview extension simply doesn't use that shape — its virtual-host URLs are CSP-blocked before any network I/O.

## CSP evidence (the root cause)

Webview HTML response carries two CSP layers; the strict meta-CSP wins:

```
# HTTP header CSP (lenient — would have allowed it):
default-src 'self'; script-src 'self' 'unsafe-inline' 'unsafe-eval' blob:;
style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' data:;
connect-src 'self' ws: wss:; worker-src 'self' blob:

# Meta CSP in webview HTML (strict, takes precedence):
default-src 'none';
script-src 'sha256-nQZh+9dHKZP2cHbhYlCbWDtqxxJtGjRGBx57zNP2DZM=' 'self';
frame-src 'self'; style-src 'unsafe-inline';
```

`vscode-resource.vscode-cdn.net` is not `'self'` from the webview's perspective → script/style blocked → extension never boots → no image fetch → silent blank editor.

## G3 — Side-by-side discriminator: **CLOSED**

- **Version tried:** code-server **4.137.0** (`b11dabdaca0d3369986975be285db92c8795cea5`, Code 1.137.0), latest release, standalone tarball, isolated XDG dirs, `--auth none`, port 18080, no proxy/daemon.
- **VERDICT: RENDERED.** In-iframe programmatic proof: `<img …Deepcode.png…>` with `naturalWidth=1456, naturalHeight=816`, visible; active tab `Deepcode.png`; no `.monaco-dialog-box`; image byte fetch HTTP 200. (Methodology note: first "RENDERED" observation was a Welcome-tab logo false positive — rejected; final verdict rests on the naturalWidth probe.)
- **Working URL shape on 4.137.0:**
  `https://vscode-remote+127-002e0-002e0-002e1-003a18080.vscode-resource.vscode-cdn.net/<abs-file-path>?version%3D1789186390489`
  — same virtual-host FAMILY as 4.112.0's failing URLs, path-style, `tkn` ABSENT, only `?version=<epoch-ms>`, served via service worker. Newer code-server's webview CSP evidently permits what 4.112.0's blocks.
- **Implication (SUPERSEDED by Follow-up Run 2):** this run changed TWO variables at once (version AND direct-vs-proxy access). The decisive run held the proxy path constant and swapped only the version — **still blocked identically**. The differentiator is the **proxy path**, not the version: not a general upstream bug (direct works), not version-specific (both versions block via proxy).

## Reproduction discrepancy (flag for fix verification)

The user-observed dialog ("An error occurred while loading the image…") did **not** reproduce in the headless capture — the failure is silent there because the dialog itself is rendered by the CSP-blocked extension JS. In the user's real browser (different service-worker/CSP-enforcement state) the extension JS evidently runs and then fails at the image step. Both paths = broken preview. **Fix verification must assert: image renders (in-iframe `naturalWidth>0` probe) — never dialog presence/absence.**

## Fix-design pointers (evidence for the developer — not prescriptions)

1. ~~Upgrade embedded code-server to ≥4.137.0~~ — **DEAD as a fix**: disproven by Follow-up Run 2 (4.137.0 through the proxy blocks identically); additionally the brew formula is capped at 4.112.0 and DEPRECATED (disabled 2027-04-11), so the standalone-binary route — and a WORKING `VSCODE_BINARY_PATH` knob (see Run 1's config-inversion defect) — is required for operability regardless.
2. **Daemon-side proxy fix (the live direction):** the proxy-served webview must permit the virtual-host origin — rewrite/inject the webview's **meta-CSP** (which takes precedence; relaxing only the HTTP header is insufficient) to allow `vscode-remote+localhost-003a8079.vscode-resource.vscode-cdn.net` for script/style, or fix the service-worker/URL wiring under the `/vscode` prefix so assets load as designed (direct mode works — 4.137.0's SW-served path-style URLs render).
3. Reusable verification harness: `capture.py` (v8, preserved below) + skill **`playwright-code-server-webbench-capture`** (id `38919a09`).

## Orphan disposition (Task 1)

| PID | What | Disposition |
|-----|------|-------------|
| 79925, 79926 | code-server 4.112.0 extension hosts, PPID=1 (proven strays of a dead instance) | **KILLED** (SIGTERM, clean exit ≤5s each, zero SIGKILL) |
| 80002, 80003 | code-server root/entry serving OLD checkout — **not orphans**: live children of `ensemble-prod` PID 48426 (port 9797) | **LEFT** (mission premise corrected: the "orphan tree" was a live daemon's editor; 48426 possibly hosts active sessions — never touched) |
| 83061, 83077 | auto-respawned extension hosts of 80003 after the stray kills | **LEFT** (healthy children of the live tree) |
| 88674, 88676, 83061, 83077, 88677, 88678 | appeared/re-parented after B's cleanup | **Resolved in Addendum:** 88674/88676 = our respawn-race orphan (KILLED); 83061/83077 = prod strays (KILLED); 88677/88678 = prod's own replacement tree (LEFT) |

## Environment restored (Task 5)

- **B's tree:** fully killed with PID-ancestry proof (dev.sh 83132 → uvicorn 83138/83140 → code-server 83206/83207 + MCP child); ports **8079 and 62682 free** post-cleanup (lsof verified).
- **C's tree:** standalone 4.137.0 stopped by recorded PIDs (84830/84844, earlier 81840/81853), port 18080 free, `/tmp/vscode-sbs-work` + `/tmp/vscode-sbs-venv` removed, brew install + `~/.config` untouched.
- **A:** audit-only + the two proven stray kills. **48426 (ensemble-prod) alive throughout, never touched; ports 8088/9797 never touched.**
- **Repo:** zero changes, zero commits (RESULTS + assets written as untracked files, per mission constraint).

## Artifacts

- **Ephemeral (dies with /tmp):** `/tmp/vscode-capture-2026-09-12/` — `network.json` (95 events), `failing-request.png`, `before-open.png`, `evidence-summary.txt`, `capture.py`, `workbench-html.html`, `dev-boot.log`, `cleanup-log.txt`, `side-by-side/{newer-version.png, network.json (61 events), render-probe.json, image-urls.json, code-server.log}`
- **Durable copies:** `.agents/tester/RESULTS/assets/2026-09-12-vscode-capture/` *(populated by final sweep — see Addendum)*
- **Reusable skill:** `playwright-code-server-webbench-capture` (38919a09)
- **Key screenshot:** failing 4.112.0 state → `failing-request.png`; working 4.137.0 state → `side-by-side/newer-version.png`

## Runtimes (all under budget)

A: ~6 min audit + ~1 min kill · B: ~22 min of 45-min budget (8 capture iterations, headless workbench quirks) · C: 15.9 min of 30-min box. Follow-up: Run 1 ~8 min · Run 2 ~9 min (both well under budget).

---

## Follow-up Run 1 — env-override attempt: **BLOCKED-BY-OVERRIDE** (halted per contract, ~8 min)

- Env name CONFIRMED `VSCODE_BINARY_PATH` (`config.py:1980-1988`: `VSCodeConfig`, `env_prefix="VSCODE_"`, field `binary_path`). Env reached the daemon process (`ps eww` proof) — but the daemon spawned brew 4.112.0 anyway.
- **Root cause (product defect):** pydantic-settings **init-kwarg-beats-env inversion** at `config.py:2820-2824` — the yaml `vscode:` section (with `binary_path: null`) is passed as init kwargs to `Config(...)`, which beats the env var. Same trap class as compaction (`config.py:2334`); the vscode section lacks the `_resolve_*` pattern (compare `_resolve_compaction_model` :2724, `_resolve_proactive_enabled`).
- Manager flow: `vscode_server_manager.py:719-755` `_resolve_binary()` reads `config.binary_path` → None → falls back to `shutil.which("code-server")` → brew; spawn `:267-279` uses it verbatim. `PUT /api/settings/editor` response even reported `binary_path: /opt/homebrew/bin/code-server`.
- **brew formula: stable 4.112.0, DEPRECATED** ("non-FOSS @github/copilot since 4.113.0; disabled 2027-04-11") → `brew upgrade` is NOT a route; **standalone binary is the only upgrade path** — and the env knob that should enable it is silently dead today.
- Evidence: `RESULTS/assets/2026-09-12-vscode-capture/proxy-4137/` (`blocked-by-override-proof.txt`, `brew-info.json`, `dev-boot.log`, `editor-enable-response.txt`, `log-baseline.txt`).
- **Fix design (developer scope, ~20 lines):** add `_resolve_vscode_binary_path` resolver before `Config(**config_dict)` (mirror `_resolve_proactive_enabled`), or drop `binary_path: null` from config.yaml's vscode section.
- Environment restored cleanly (uvicorn-first per LESSONS #7; ports 8079/57097 free; prod family untouched). Browser capture not run — attempt 2 completes it via PATH-shim (below).

## Follow-up Run 2 — decisive version-isolation run (PATH-shim): **BLOCKED — version is NOT the fix** (~9 min)

- Mechanism: `VSCODE_BINARY_PATH` env is dead (Run 1 config inversion), so the binary swap went through `shutil.which` control — `/tmp` shim dir prepended to the daemon's PATH; the manager spawned the 4.137.0 tarball binary with identical args.
- **Proof it was really 4.137.0 under the proxy:** spawned cmdline `/tmp/vscode-proxy-4137/code-server-4.137.0-macos-arm64/lib/node … --bind-addr 127.0.0.1:0 --auth none --user-data-dir data_dev/vscode-user-data <main checkout>`; PUT response `binary_path: /tmp/vscode-proxy-4137/shim/code-server`; daemon log `code-server started: pid=92082 port=58934`; webview URL content-hash `stable-b11dabdaca0d3369986975be285db92c8795cea5` = the 4.137.0 build id.
- **VERDICT: BLOCKED** — in-iframe naturalWidth probe TIMEOUT: 0 rendered images, no dialog (silent-blank variant, same as 4.112.0 via proxy).
- **Exact CSP violations (console, verbatim class):** `Loading the stylesheet 'https://vscode-remote+localhost-003a8079.vscode-resource.vscode-cdn.net/private/tmp/vscode-proxy-4137/code-server-4.137.0-macos-arm64/lib/vscode/extensions/media-preview/media/imagePreview.css' violates … "style-src 'self' 'unsafe-inline'"` and the same for `imagePreview.js` vs `"script-src 'self' 'unsafe-inline' 'unsafe-eval' blob:"`. Virtual host still encodes the PROXY port 8079; blocked paths point into the 4.137.0 tarball. Note: the console violations are reported against the **header** CSP fallback directives — the meta-CSP (only bundled-sha256 differs: `sha256-24Qq…` vs 4.112.0's `sha256-nQZh…`) blocks at least as strictly.
- **Three-run matrix (the decisive evidence):**

| Run | Version | Access | Result |
|-----|---------|--------|--------|
| B1 (G1/G2) | 4.112.0 (brew) | `/vscode` proxy | BLOCKED (meta-CSP) |
| Run 2 | 4.137.0 (tarball) | `/vscode` proxy | **BLOCKED — identical** |
| C (G3) | 4.137.0 (tarball) | direct loopback | **RENDERS** |

  Version held constant between Run 2 and C → **the proxy path is the sole differentiator.** Fix = daemon-side proxy CSP/SW/URL wiring. Upstream code-server is exonerated for the direct path; its restrictive webview CSP merely makes it unforgiving of the proxy's origin wiring.
- Harness note: `capture.py` extended (iframe render probe + console capture). Deterministic-find pollution discovered — evidence PNGs under `.agents/tester/RESULTS/assets/` sorted first — target pinned to `Deepcode.png` by absolute path; future runs of the find rule must add `-not -path '*/.agents/*'`.
- Evidence: `RESULTS/assets/2026-09-12-vscode-capture/proxy-4137/` — `attempt2-verdict-BLOCKED.txt`, `blocked-proxy.png`, `network-4.137-via-proxy.json` (79 events), `console-4.137-via-proxy.log`, `shim-code-server.sh`, `dev-boot2.log`, `editor-enable2-response.txt`, `log-baseline2.txt`, `capture.py`.
- Environment restored: uvicorn-first cleanup, all spawned PIDs gone (92036/92042/92043/92044/92082/92094), ports 8079/58934 free, no `data_dev`-signature survivors, prod family untouched; tarball + extraction removed after the evidence copy verified.

## Final E2E — MERGE GATE: `feature/fix-vscode-image-preview` @ `7ccc8a2f` — **PASS** (all 3 jobs green)

Workers: B a77ba1fb (Jobs 1+2+stretch, ~10 min) · D `regression-spotcheck` bcb3094f (Job 3).

### Job 1 — Fresh venv end-to-end (brotli dep proof): **PASS**
- Bare `uv sync` RC=0 ("Resolved 124 packages / Audited 117"); `import brotli` OK (1.2.0); declared `pyproject.toml:49`; import sites `vscode_proxy.py:338/:381`; daemon boot 7s; code-server start logged via PUT editor.

### Job 2 — MAJOR-4, both states, FRESH PROFILES (OFF first): **PASS**
| State | meta-CSP (served webview doc) | depth-N probe | image request | Verdict |
|---|---|---|---|---|
| **OFF** (`ENSEMBLE_VSCODE_WEBVIEW_CSP_FIX=0`) | strict pre-fix shape — NO `img-src`, NO `media-src`, NO wildcard (verbatim archived) | imgCount=0 at ALL depths (recursive probe) | never attempted (extension CSP-blocked) | **BLOCKED ✓** |
| **ON** (default) | carries the fix — `img-src data: blob: https://*.vscode-resource.vscode-cdn.net` + `media-src` + widened script-src/style-src wildcards | **naturalWidth=1456 @ depth 2** | **200**, content-length 1525753, image/png, PNG header | **RENDERS ✓** |

- Round-2 cache confound eliminated: separate browser launches on separate brand-new profile dirs + full daemon restart between states (env is load-time).
- Fix is surgical: OFF↔ON differ ONLY by the wildcard additions + new directives; bundled sha256, `default-src`, `frame-src`, `style-src 'unsafe-inline'` identical.

### Stretch — 4.137.0 through proxy via env route: **PASS**
`VSCODE_BINARY_PATH=<tarball>` now honored end-to-end (PUT `binary_path` = tarball; spawned cmdline = tarball binary; webview hash `stable-b11dabd…`) — live-proof of the `_resolve_vscode_binary_path` resolver (the attempt-1 inversion is dead). Fixed meta-CSP shape; **renders nw=1456 @ depth 2**.

### Job 3 — Regression spot-check: **GREEN**
- `tests/integration/test_vscode_proxy.py` → `70 passed in 0.88s` (verbatim)
- `tests/unit/test_vscode_binary_path_config.py` → `22 passed in 0.18s` (verbatim)
- `tests/integration/test_vscode_security_integration.py` → `1 failed, 7 passed in 2.04s`; the ONLY failure is `TestC1PathTraversal::test_c1_valid_repo_folder_not_blocked` (`DID NOT RAISE httpx.ConnectError`, :329) — **byte-matches QUARANTINE.md row 32** (base-evidenced at `e866c116`, branch-exonerated). Base-verify worktree skipped (unambiguous).

### Merge-gate verdict: **PASS — gate clear.**

### New harness lessons (also in LESSONS)
- VS Code web nests webviews INSIDE webviews — render probes must recurse (depth-1 saw imgCount=0 while the image rendered at depth 2; v1 artifacts kept as `*.v1.*`).
- `uvicorn --reload` silently absorbs SIGTERM — daemon cleanup needs SIGKILL (uvicorn master first, trap #7 order unchanged).

### Artifacts
`RESULTS/assets/2026-09-12-vscode-capture/final-e2e/{off,on,stretch-4137}/` — each: `meta-csp.txt`, `probe-result.json`, `network.json`, `console.log`, screenshot (+ `*.v1.*` forensics).

### Environment restored
All spawned trees killed (uvicorn-first; SIGKILL after the SIGTERM-absorb discovery); port 8079 free; no `data_dev`/4.137.0-signature survivors; prod 48426 + ports 9797/53280/8088 untouched; no repo edits beyond `RESULTS/assets/`; no commits.

## Addendum — final attribution sweep & environment-restore closure (04:06–04:30Z)

**Attribution of the post-cleanup processes:**
- **88674/88676 (listener 53279) = OUR respawn-race orphan.** Created during B's cleanup: the code-server root (83206) was killed *before* its daemon parent; the daemon's vscode manager auto-respawned a replacement inside that window; it reparented to launchd (PPID=1) when the uvicorn worker died. Signature match with B's tree: `data_dev/vscode-user-data` + NEW checkout. (Trap + prevention rule recorded in LESSONS #7.)
- **88677/88678 (listener 53280) = prod daemon's OWN replacement tree** (88677 PPID=48426) — prod replaced its old 80002/80003 tree by itself between 04:06:55Z and ~04:24Z. Not our doing; not ours to touch.
- **83061/83077 = strays** orphaned by that prod self-replacement (same proven class as the sanctioned 79925/79926).

**Final kills (04:30:02–04:30:22Z; all 4 PIDs re-verified at kill time; SIGTERM only, zero SIGKILL, zero collateral):** 88674 ✓, 88676 ✓ (cascaded with root), 83061 ✓, 83077 ✓ — all confirmed gone by direct re-check.

**End state (verified):**
- code-server population = ONLY the prod family: 88677/88678 + their self-healed extension hosts 90115/90129 (ancestry 90115 → 88678 → 88677 → 48426 → launchd).
- 48426 (`ensemble-prod`, port 9797) alive throughout (~26h uptime), never touched.
- Ports: **8079 EMPTY** · **53279 EMPTY** · 9797 + 53280 prod-owned and untouched · 8088 never involved.
- **Zero orphaned code-server processes** — end state is cleaner than found (found state carried strays 79925/79926).

**Durable evidence:** 11/11 files `cp -p`-preserved to `.agents/tester/RESULTS/assets/2026-09-12-vscode-capture/` (mtime/mode verified; /tmp originals untouched): `capture.py` (reusable harness), `network.json` (95 events), `side-by-side-network.json` (61 events), `evidence-summary.txt`, `failing-request.png`, `before-open.png`, `newer-version.png`, `render-probe.json`, `workbench-html.html`, `cleanup-log.txt`, `code-server.log`.

 `cleanup-log.txt`, `code-server.log`.

