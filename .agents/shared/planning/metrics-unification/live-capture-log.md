# Live UI Capture + Developer-User Quality Judgment — `/metrics` & `/resources`

- **Date:** 2026-11-03, 15:56–16:20 UTC (captures 16:12–16:17 UTC)
- **Target:** LIVE production daemon `http://127.0.0.1:9797` (ensemble_prod), FE served by the same daemon
- **Executor:** tester (leader) — 5 read-only workers: auth census, browser intel, 2 capture lanes, retention probe
- **Posture:** READ-ONLY. Browser GET-only traffic; no writes to live DB / state.json / releases; no restarts; no systemd; no promote ops; no git add/commit.
- **Evidence dir:** `.agents/shared/planning/metrics-unification/screenshots/` (12 PNG + 12 JSON sidecars + `network-census.json` + `index.json`)

---

## 0. Executive verdict

| Page | State captured | One-liner |
|---|---|---|
| `/metrics` | **Fail-closed, zero data** | Truthful, well-worded banner — then a page that renders *nothing*: 0 stat cards, 0 SVGs page-wide, 0 family rows. Auth is OFF on the daemon, so the central-metrics surface is structurally dark. |
| `/resources` | **Live, real data** | Genuinely useful daemon vitals at a glance (CPU/RSS/threads/cgroup/throttle); weak on attribution (0% coverage, no per-instance rows), weak on trend semantics (no y-axis, unlabelled event dots). |

For the two developer questions: **"why is this instance slow right now?" → partially** (shape yes, cause/culprit no). **"what changed overnight?" → no** (1h-window capture, no y-axis, unlabelled events; retention probe below). Ranked friction list in §7.6.

---

## 1. Live environment facts (verified, not assumed)

```
GET /livez  → 200  {"status":"alive","uptime_seconds":1128.31→2119.02,"version":"0.28.9"}
GET /readyz → 503  degraded: checkpoint_saver sentinel read (aget_tuple) timeout; grace ACTIVE
                 (scope=load, holds=6, expires 16:04:22Z), inflight_turns=8, leak_signal=false,
                 pool 1/15 checked out. Pre-existing at first read; NOT caused by this probe
                 (uptime monotone throughout; known routed checkpoint-amplification lane).
```

- **Live release = v0.28.9**, `current → releases/v0.28.9` (symlink mtime Nov 3 15:43), daemon epoch **15:43:16Z**, MainPID 3175816, NRestarts 0 across the whole capture window.
- ⚠️ **Metadata correction for the record:** project KV `staged_release_v0289_composition.promote` still says "NOT performed" — stale. v0.28.9 went live ~15:43Z today (before our first probe). The Timeline capture's ~15:42–43 memory V-collapse **is this promote/restart**, visible but unannotated in the UI (see §7.5).
- FE = Angular SPA shell (`<app-root>`, title "Frontend"), assets root-relative, entry `main-SAUX6EY3.js` (215 KB) + `scripts-DN54Y2NQ.js` (3.5 MB) + `styles-S2UMLXTA.css`; lazy chunks per route.
- **Source-of-truth trap:** `/home/nea/ensemble-src` is a 747-commits-stale checkout (v0.27.8-era, no `/metrics` route at all). All FE facts were read from `/home/nea/ensemble-src-wt-stage-v0289` @ `7fcf1bd71` (= live payload tree) and cross-checked against rendered DOM.

## 2. Auth discovery (mechanism only — zero secret values recorded)

**Design (auth ON):** OAuth2/OIDC via external IdP. Browser lane = server-minted cookie `ensemble_sid` (HttpOnly, SameSite=Lax, `Path=/`) + readable double-submit `ensemble_csrf` (= SHA-256 of the session id, echo header `x-csrf-token`) — `daemon/auth/cookie.py:69,71`. Machine lane = `Authorization: Bearer <opaque>`, scopes held **server-side** on the session/token row → `AuthContext.scopes` (`daemon/auth/context.py:57`), enforced by `scope_required("maintenance:read")` (`daemon/auth/decorators.py:160-184`) at `daemon/routers/metrics.py:158,581,630,703`. Unauthenticated browser hit on a protected path → 302 `/api/auth/login?return_to=…`.

**LIVE state: auth is OFF entirely.**

| Evidence | Where |
|---|---|
| `auth:` / `enabled: false` in the live install config | `/home/nea/agents-ensemble/config.yaml:127-128` |
| Zero `ENSEMBLE_AUTH_*` keys in the live `.env` (50 keys inventoried, names+lengths only) | `/home/nea/agents-ensemble/.env` |
| `AuthMiddleware` never registered; `app.state.auth_config` unset | `daemon/api.py:5134, 5257-5264` |
| `/api/auth/{login,callback,me,logout}` → **404** (router mounts only `if auth_enabled:`) | `daemon/api.py:4684` |
| Metrics door 503s **before** scope check / registry read (fail-closed on `0.0.0.0` bind) | `daemon/routers/metrics.py:136-152` |

**Proof no credential helps:** dummy bearer (`probe-dummy-not-a-real-token`, not a secret) → `/api/metrics/*` **still 503**, `/api/resources/*` still 200.

**Consequence for this commission:** the authenticated capture set is **structurally impossible without user action** (enable `auth:` + IdP and restart, then OIDC login or operator `daemon/auth/cli.py issue-token --scopes maintenance:read`). Per the brief, we captured the truthful unauthenticated state and documented the gap — no faking. Alternative the user owns: the v0.28.9 agent-tool lane (`metrics_families/snapshot/history`, allow-listed to maintenancer/devops/wanderer — excludes tester/worker, and would require spawning an instance on prod, out of scope).

## 3. Capture method (exact tooling + commands)

- **Driver:** Node Playwright **1.60.0** from `/home/nea/ensemble-src/frontend/node_modules/playwright` (python Playwright absent on this host; raw chromium FATALs without `--no-sandbox` — AppArmor/userns; Playwright implies it).
- **Timing contract:** `page.goto(url, {waitUntil:'domcontentloaded', timeout:30000})` → best-effort `networkidle` (15 s cap, always times out — an SSE stream `/api/notifications/stream` + 8 s `/api/resources/*` polls never settle) → fixed **3 s settle** → screenshot.
- **Viewports:** 1600×1000 (desktop) and 900×1000 (narrow probe of `/metrics`).
- **Instrumentation per page:** every `/api/` response logged as `{method, status, url}` (method = GET-only proof), console errors, pageerrors; each capture run wrapped in `timeout 120`.
- **Interactions:** tab switches, gear-menu open, mouse hover, one Retry click — all verified GET-only in the network log.
- Canonical capture script shape (used for all 12 PNGs):

```js
const { chromium } = require('/home/nea/ensemble-src/frontend/node_modules/playwright');
const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
const context = await browser.newContext({ viewport: { width: 1600, height: 1000 } });
const page = await context.newPage();
const api = [];
page.on('response', r => { if (r.url().includes('/api/'))
  api.push({ method: r.request().method(), status: r.status(), url: r.url() }); });
await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 30000 });
try { await page.waitForLoadState('networkidle', { timeout: 15000 }); } catch {}
await page.waitForTimeout(3000);
await page.screenshot({ path: out, fullPage: true });   // viewport-only where noted
```

- API census (curl, GET only): `curl -s -o /dev/null -w '%{http_code} %{content_type}\n' http://127.0.0.1:9797<url>` per endpoint, body excerpts ≤250 chars, secret-scrubbed.

## 4. HTTP status census (all GET, :9797)

### `/api/metrics/*` — 503 fail-closed (browser census: 5 calls, all 503, all GET; `series` never called — Charts poll predicate never opens with an empty catalog)

| URL | Status | Body |
|---|---|---|
| `/api/metrics` , `/families` , `/snapshot` , `/series` | **503** | `{"detail":{"error":"AUTH_NOT_CONFIGURED","message":"Auth not configured (fail-closed)"}}` |
| Retry click behavior | 1× `GET /api/metrics/families → 503` | UI byte-identical before/after — no feedback loop, but also no remedy |

### `/api/resources/*` — 200 open, no scope gate (real data at capture time)

| URL | Status | Shape |
|---|---|---|
| `/api/resources/live` | 200 | daemon pid/rss(633 MB)/threads(74)/fds, cgroup current/peak/anon/file-cache/slab, attribution |
| `/api/resources/history?window=&resolution=` | 200 | points[] `{t, daemon_cpu, daemon_rss_mb, …}` |
| `/api/resources/self-cost` | 200 | `{cpu_pct:null, cumulative_cpu_s, rss_budget_mb:50, …}` |
| `/api/resources/anomalies?window=&limit=` | 200 | **rows exist** (anom-000003 cpu peak 75.55 severity medium, anom-000004 fds, …) — FE renders no anomaly table (see §7.2) |
| `/api/resources/anomalies/{id}/evidence` | 200 | per-anomaly detail |
| `/api/resources/traceback?t0=&t1=` , `/control-capabilities` | 200 | window detail; `enforcement:"observe-only"`, all actions unavailable, policy_rows 0 |
| `/api/resources` (bare) | 404 | no bare route |

### FE routes + shell bootstrap

`/metrics` and `/resources` → 200 `text/html` SPA shell (identical 9257 B). Bootstrap 200s on every page: `/api/health`, `/api/projects`, `/api/settings/{plane,editor}`, `/api/migration/availability`, `/api/maintenance/checkpoint-cleanup/availability`, `/api/missions`, `/api/queues/defer-blocked`, `/api/instances`, `/api/jobs`, `/api/agents` (root), `/api/notifications/stream` (SSE). Benign 503 noise: `/api/prompts/pending-count` (unrelated to metrics). 566 browser requests observed across the unauth set + 81 across the deep set — **0 non-GET**.

## 5. Capture manifest

Dir: `.agents/shared/planning/metrics-unification/screenshots/` — each PNG has a same-name `.json` sidecar (url, viewport, auth_state, per-capture `/api/` responses, console errors, DOM excerpts).

| File | What it shows (one line) | Viewport / mode | Substrate image_id |
|---|---|---|---|
| `01-metrics-unauth.png` | Fail-closed banner ×2 (page + tab), Overview active, **no data anywhere** | 1600×1000 fullPage | `abe2f63055cb478da81c3ec602176948` |
| `01b-metrics-unauth-narrow.png` | 900 px: shell holds, banners single-line — **banner-2 text collides with its Retry button** | 900×1000 fullPage | `a6fc9fa55a634654b526356b75c5185f` |
| `02-metrics-overview.png` | Landing state + verbatim banner copy (`02`–`04b` = deep lane) | 1600×1000 fullPage | `e33e8489d7304a6c8188943ba387a403` |
| `03-metrics-charts-tab.png` | Charts tab: hint only — "Waiting for the family catalog…"; 0 `<svg>` page-wide | 1600×1000 fullPage | `d4dafd8dd0344517827a347a5bfb16da` |
| `04-metrics-families-tab.png` | Families tab: headers `Family\|Role\|Availability\|Series\|Names`, **0 rows**, "No families are registered." | 1600×1000 fullPage | `5d5ccf9054974a4c87c09ea53e83dcb0` |
| `04b-metrics-banner-closeup.png` | Banner legibility close-up (viewport-only) | 1600×1000 viewport | `2caf6028f0ec43f39cb874577bb32d55` |
| `05-resources.png` | Live tab, real data @16:16:21Z: CPU 68.9 %, RSS 633 MB, 74 thr, 50 FDs, cgroup 6178/6829 MB, attribution 71 MB vs 1119 MB unattributed **0 % coverage** | 1600×1000 fullPage | `621efc276dde4495b7657768c4fc5127` |
| `05b-resources-timeline.png` | Timeline: 3 plotted series (RSS 348.9–874.7, Group 581–6 784, CPU 7.4–105.4), 15:16→16:15 UTC, **4 unlabelled event dots**, 1h chip active | 1600×1000 fullPage | `69e214bb17864d49b81f0220a4bea02b` |
| `05c-resources-timeline-tooltip.png` | **No tooltip exists** — 8-position hover sweep, zero overlays (truthful chart-only shot) | 1600×1000 viewport | `0c71fb0160654caa981ec3a44fa5a4e1` |
| `05d-resources-anomalies.png` | **No Anomalies section exists in the FE** — API rows exist, UI shows none; frame shows ring-drain em-dash transient | 1600×1000 fullPage | `259e5e6ebe4c4c6e94a243504fe58b44` |
| `06-app-shell.png` | App root: "NEW CONVERSATION / Choose an agent", header nav Instances·Sources·Schedules·Jobs·Skills·Persona·Plan, `v0.28.9` badge | 1600×1000 fullPage | `97139be19f8148a1b0b46e8c23a33bb4` |
| `06b-nav-gear-menu.png` | Gear menu OPEN, 9 entries: Resources = 5th, Metrics = 7th | 1600×1000 viewport | `6ba90ca39f4e40298ff534ad564d85bd` |

All 12 persisted to the daemon tmp-image substrate: `feature=fe-ui-audit`, `version=v0.28.9`, per-route `page`; byte-exact roundtrip verified (sha256 disk == store; 8/8 + 4/4 read-back).

## 6. Safety record (including disclosed deviations)

- **Browser traffic: 100 % GET, 0 non-GET to :9797** — asserted in-script after *every* capture (runs would have aborted), plus final sweeps (566 + 81 requests). Interactions: tab switches, gear-menu open, hover, one Retry click. No action/control button touched. No `Retry` on /resources; anomaly "actions" are observe-only/unavailable anyway.
- **Deviation 1 (disclosed by deep-capture worker):** substrate rows for `02–04b` written via the store's own code path (`daemon/services/tmp_image_store.py`) directly against the daemon data dir — the `image_save` tool requires literal base64 in a tool argument (~44 KB/PNG), unreliable to hand-transcribe. Same save contract, 3-way sha match, read-back verified via `image_list`/`image_get`.
- **Deviation 2 (disclosed by unauth-capture worker):** its 8 rows went through the daemon's auth-free `POST /api/tmp_images` door (the in-project documented upload for this exact substrate, ≤3/request, 3 batches). **This is the one place the letter of "GET-only on :9797" was exceeded — scoped solely to uploading the evidence images the commission itself ordered into the substrate it named.** No other daemon state touched; byte-exactness proven 8/8 (sha256 + ETag == sha[:16]).
- **Collision handled:** the deep worker briefly mis-tagged the unauth set's 8 PNGs `page=/metrics` (shared-glob mistake), then removed exactly those 8 rows by id — files untouched. The unauth worker then re-persisted them with correct per-route tags.
- No installs (no npm/pip/apt/npx install), no `package.json`/lockfile/`node_modules` changes, no dev server, no FE build, no daemon restart/signal, no systemd, no DB connection, no writes to `releases/`/`state.json`, **no git add/commit** (output dir untracked only; dirty files in the checkout are other lanes' pre-existing WIP). All spawned browsers closed (verified). Live fence: `livez` uptime monotone 1128→2119 s, MainPID 3175816 unchanged, NRestarts 0.

## 7. Developer-user judgment (the heart of the deliverable)

### 7.1 Genuinely usable for the two questions?

**"Why is this instance slow right now?" — Partially; shape yes, cause no.**
- What works: `/resources` Live is a real vitals board. In one glance: daemon CPU **68.9 %**, RSS 633 MB, 74 threads, cgroup 6178 MB (of which 4 965 MB file cache, 291 MB slab), **zero cgroup CPU throttling** (0.00 s / nr_throttled 0) — throttling is *ruled out* legibly. Cumulative counters let you do math the UI doesn't: cgroup CPU 1403 s over 32:38 uptime ≈ **72 % average since boot** — so 68.9 % is chronic, not a spike. Sampling is healthy (63 samples, 0 ticks skipped). That is genuine diagnostic value.
- What blocks the answer: the page is **daemon-scoped, not instance-scoped**. The Attribution panel — the designed answer to "which instance" — renders **zero per-instance rows**, only aggregates: 71 MB attributed vs **1 119 MB unattributed**, "Coverage 0 %", under an ESTIMATED badge. And the arithmetic doesn't reconcile on screen: 71+1 119 = 1 190 MB ≈ anon 901 + slab 292, which is ~2× RSS 633 MB and ~1/5 of cgroup 6 178 MB; no denominator for "0 %" is stated. An engineer will burn ten minutes on that panel and leave *more* confused than on arrival. There is also no DB-pool / loop-lag / checkpoint / job-backlog correlation on the page — the actual drivers of this daemon's historical slowness (2026-10-26 class) are invisible here.
- `/metrics` contributes nothing today (fail-closed; and even lit, it is central/daemon-scope families, not per-instance).

**"What changed overnight?" — Half-yes: the data exists; the presentation wastes it.**
- The 24h/7d chips are backed by **real durable history** (probe §7.7: 24h = 2 723 raw 30 s points spanning the full day; 7d = 13 387 minute-tier points across all 166 clock-hours, 10 downtime holes, largest 4.6 h on 11-01) — the raw material for the overnight answer is genuinely there.
- But the chart has **no y-axis at all** — no labels, no units, no gridlines; the three series are independently min-max normalized (CPU 7.4–105.4 % shares frame height with memory up to 6 784 MB), so vertical position means "high *for that series*", never an absolute level. Without units you cannot say whether 875 MB or 6.8 GB is the alarming number.
- The four event markers are **unlabelled colored dots** (red/amber/red/blue) with dashed drop-lines — a third color vocabulary matching neither the series palette nor the chip palette, no legend entry, no tooltip (hover provably renders nothing). Worse: the anomaly journal behind them is **process-local** (in-RAM `deque(maxlen=256)`, reset at every restart, cold-boot replay capped ~3.2 h) — the 24h/7d anomaly sets are *identical* to the 1h set, so an overnight view can show history but **never overnight events**.
- The one event a developer would most want annotated — a restart — is sitting right there **unlabeled**: the ~15:42–43 V-collapse (RSS→0, group-mem→0, CPU spike, then regrowth *above* the old plateau) is exactly the **v0.28.9 promote/restart at 15:43:16Z**, and the UI never says "restart/deploy happened here".

### 7.2 What looks thin or placeholder-ish

1. **The entire `/metrics` body below the banners** — 0 stat cards, 0 charts (`<svg>` count = 0 page-wide), 0 family rows, 0 pills, ~67 % of the viewport is empty void with no skeleton/empty-state art.
2. **"the last known values are kept"** — promised in the Overview banner, nothing is kept or shown (null snapshot ⇒ nothing to retain). Copy promises a behavior the state can't deliver.
3. **Families empty-state: "No families are registered."** — actively misleading: it reads "the registry is empty" while the real cause is the closed door. The v0.28.6 registry ships ~16 live families; the FE renders a 503 as an empty catalog.
4. **Attribution panel** — three aggregates + footnote + blank space where per-instance rows should be.
5. **Self-cost overruns "Count 0 / Last never UTC"** — reads like a placeholder.
6. **Threads + Active instances chips enabled but plotting nothing** (no trace, no range annotation, no "no data" note) — silent gaps.
7. **Em-dash ring-drain transient** on Live (~30 s refill vs 60 s drain): scalars flip to "—" with no explanation — looks broken when it's documented behavior the UI doesn't surface.
8. **Anomalies**: the API has rows (cpu peak 75.55 medium, fds, …) — the FE has **no anomalies list at all**; they surface only as the anonymous dots on Timeline.

### 7.3 What is confusing / requires knowledge the UI doesn't surface

- **Two memory universes** (process RSS 633 MB vs cgroup 6 178 MB) plus a third attribution universe (1 190 MB) — no reconciliation, no hint that file cache dominates the cgroup number.
- **"Coverage 0 %"** with no denominator; the ESTIMATED badge is good honesty, the arithmetic is not.
- **Three color vocabularies on one panel** (series fills ≠ chip colors ≠ marker colors) and **no y-axis** — the normalization semantics are invisible.
- **"MONITOR COST" strip + "level full"** jargon; `Self-cost overruns` has no unit/threshold definition on screen.
- **Two observability pages with opposite security postures**: `/metrics` fail-closed dark, `/resources` wide open. A user who reads the metrics banner then finds Resources fully populated will conclude "metrics is broken", not "auth is off".
- **Discoverability**: Metrics and Resources are **not in the top nav**; they are entries 7 and 5 of 9 in an unlabeled icon-only gear menu, ungrouped, where Resources and Maintenance share the same wrench glyph. A new user scanning the header (Instances·Sources·Schedules·Jobs·Skills·Persona·Plan) finds no observability affordance at all.
- **Retry as the remedy for a configuration error** — offered twice, cannot ever succeed, no env-var/doc pointer.

### 7.4 What breaks when data is dark/stale — does the UI say WHY?

- `/metrics` auth-dark: **yes, excellently** — the banner names the exact cause ("fail-closed — auth is not configured on the daemon"). This is the best dark-state copy on either page. Deductions: (a) duplicated banner (same sentence twice within ~130 px), (b) the Families tab simultaneously claims "No families are registered." — contradicting the banner, (c) the Charts tab shows a *different, weaker* message ("Waiting for the family catalog…") that names no cause, (d) no remediation (no config key, no doc link), (e) Retry twice.
- `/resources` dark spots: **mostly silent** — enabled-but-empty chips, unexplained em-dashes, anonymous dots, no per-instance rows and no note about it. The page's own honesty devices (ESTIMATED badge, "never hidden" footnote) are good, but the gaps themselves are unexplained.

### 7.5 Readability at a glance (Discord-paste test)

- **The ~15:42 V-collapse: YES** — the loudest thing in the frame, reads even as a thumbnail. Best visual on either page.
- **A moderate CPU excursion: WEAK** — translucent overlapping fills + no y-axis + per-series normalization muddy the amplitude; you see "bumpy", not "how high".
- **Live-tab numbers: legible** but labels are low-contrast slate-on-dark (CGROUP CPU, nr_throttled, footnotes noticeably fainter than values).
- **Narrow 900 px: shell holds** (no overflow/wrap), but **banner-2's text collides with its own Retry button** (text ends x≈768, button starts x≈757 — button appears out of text flow; a real CSS defect on any longer message), and the nav row has only ~55 px slack (breaks somewhere ~820–860 px).

### 7.6 Friction list, ranked by how much it blocks a real investigation

1. 🔴 **`/metrics` is dark by install posture** — auth OFF ⇒ the whole central-metrics surface (the page built to answer these questions) serves nothing. *User-owned decision:* enable auth + IdP + `maintenance:read` (or accept dark; or use the agent-tool lane).
2. 🔴 **Attribution panel: no per-instance rows + unreconcilable numbers** (71 MB / 1 119 MB / 0 % vs RSS 633 vs cgroup 6 178). The one panel designed to name a culprit is aggregate-only and self-contradictory.
3. 🔴 **Timeline has no y-axis / independent per-series normalization** — absolute levels invisible; "which number is alarming" unanswerable from the picture.
4. 🟠 **Unlabelled event markers + no restart/deploy annotation + no anomalies list** — the four dots ARE the anomaly findings (timestamps match anom-000003 cpu spike / anom-000004 fds ramp / anom-000005,6,7; `kind`, `severity`, `peak`, `shape`, `series_key` all already in the API), the marker journal is process-local and cannot survive a restart (≤3.2 h replay cap), and the chart's biggest event — the v0.28.9 promote/restart — is unlabeled. Every word needed is in the API; none reaches the pixels.
5. 🟠 **No time dimension on Live** (no sparkline/min-max/avg-since-boot) — "new or chronic?" requires the cumulative-CPU mental math the UI could do itself.
6. 🟠 **Dark-state copy contradictions on /metrics** — "No families are registered." vs the fail-closed banner vs the Charts "waiting" hint: three zero-states, three stories.
7. 🟡 **Discoverability** — gear-menu burial (5th/7th of 9), no top-nav entry, duplicated wrench icon.
8. 🟡 **Silent no-data on /resources** — enabled chips with no traces, em-dash drain transients, no tooltips (provably none exist).
9. 🟡 **900 px banner-2 text/Retry collision** + nav near breakpoint — small CSS defects, real.
10. 🟢 Header indicator semantics (`1/1`, `•8`) unexplained; range-format inconsistency on the legend (`348.9–874.7` vs `581–6,784`).

**What genuinely works** (kept honest): the fail-closed banner copy is specific and truthful; Live-tab daemon vitals are dense and correct; throttling ruled out at a glance; ESTIMATED/"never hidden" honesty devices; the restart signature is instantly readable; clean shell, visible version badge.

### 7.7 Retention probe — 24h/7d reality (GET-only, live :9797, 16:33–16:45 UTC)

| `window=` | status | points | span | resolution |
|---|---|---|---|---|
| 15m | 200 | 28 | 16:18:52→16:32:56 | raw 30 s |
| 1h | 200 | 112 | 15:33:48→16:32:56 | raw 30 s (one 111 s hole) |
| 24h | 200 | **2 723** | **2026-11-02 16:33:53 → 11-03 16:32:56** | raw 30 s; all 25 clock-hours present; 3 holes ≤13 min |
| 7d | 200 | **13 387** | **2026-10-27 16:34 → 11-03 16:32** | minute tier 60 s (med 52 s); all 166 clock-hours present; 10 downtime holes, largest **4.6 h** (11-01 09:11→13:47) |
| 30d | **400** | — | `{"detail":{"error":"invalid window '30d'; expected one of ['15m','1h','24h','7d']"}}` | max supported = 7d |

- **History is durable and genuinely answers overnight questions** — the 24h/7d chips are backed by real data (raw tier retained 48 h, minute tier ~14 d; DB `resource_daemon_bucket`). Caveat: `cgroup_mem_mb` is sparse in the 7d minute tier (4 002/13 387 points).
- **Anomaly markers are process-local and structurally CANNOT span overnight** — `FindingJournal` is an in-RAM `deque(maxlen=256)` (`daemon/services/resource_anomaly.py:908`), reset on every daemon restart; cold-boot replay is capped at 192 minute rows ≈ **3.2 h**. Live proof: daemon booted 15:43:16Z; 13 anomaly rows exist (7 memory / 2 cpu / 2 fds / 2 threads; 5 low / 4 medium / 4 high; all `instance_id=None`) and **every `t_start` falls inside 15:00–16:15 today** — the 24h and 7d anomaly sets are identical to the 1h set. No marker older than ~1.5 h can ever render.
- **The four unlabelled Timeline dots ARE these anomaly findings** — dot positions (~15:40 / ~15:45 / ~15:50 / ~15:55) match anom-000003 (cpu spike 75.55), anom-000004 (fds ramp, high), anom-000002/5/6 (memory/threads spikes), anom-000007 (anon high). The API already carries `kind/severity/peak/shape/series_key` per finding; the FE renders them as anonymous colored dots with an unexplained third color vocabulary.
- **Judgment impact:** "what changed overnight?" is answerable from **history** (switch the chip — data exists), never from **markers** (nothing survives a restart), and remains hampered presentationally by the missing y-axis / normalization (§7.1).

## 8. What was NOT captured, and why (candid gaps)

1. **Authenticated `/metrics` (charts, stat cards, populated Families table, activation panel in the ON state)** — structurally impossible: daemon auth is OFF; no credential can open the door (proved with dummy bearer); enabling auth requires a daemon restart + IdP = user decision, explicitly out of read-only scope. Everything reachable unauthenticated *was* captured, including every tab and the Retry path.
2. **Metrics hover/tooltip detail** — nothing renders to hover; there is no chart to expand.
3. **Resources Timeline at 24h/7d** — range chips exist but were not clicked during capture (the retention probe answers the data-existence half; the visual half remains uncaptured).
4. **`/resources` anomalies UI** — does not exist in the FE (captured the negative proof instead).

## 9. Follow-ups

- **User-owned:** (a) enable auth + issue a `maintenance:read` (or `metrics:read`) token to light `/metrics` up, then re-run this capture for the authenticated set; (b) note `/readyz` degraded at capture time (checkpoint_saver sentinel timeouts — known routed lane, pre-existing).
- **FE lane candidates** (from §7.6): y-axis + normalization semantics; labelled event markers + restart/deploy annotations + an anomalies list; per-instance attribution rows + reconciled coverage %; single banner + remediation link instead of double Retry; empty-state copy branched on 503-vs-empty; banner CSS collision at narrow widths; chip-vs-trace mismatch; **persist the anomaly journal** (today an in-RAM `deque(maxlen=256)` — every finding dies at the next restart, so the 24h/7d marker lanes can never mean anything; even a small DB-backed journal would change that); surface the 7d minute-tier `cgroup_mem_mb` sparsity (4 002/13 387 points).
- **Record corrections:** project KV `staged_release_v0289_composition.promote` is stale (v0.28.9 IS live since 2026-11-03 ~15:43Z).

*No secrets appear in this document. Auth env vars are recorded as names/presence/length only.*
