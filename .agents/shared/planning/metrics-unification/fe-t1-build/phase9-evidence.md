# Phase 9 evidence — verification, bundle guard, packs, close-out (2026-11-04)

Lane: wi-32a99618 (FE-only T1 metrics dashboard) · worktree
`/home/nea/ensemble-src-wt-fe-metrics-dashboard` · branch
`feature/fe-metrics-dashboard` · HEAD `d2b4c2505` at phase start.
Every command below was executed with the PINNED interpreter
`/home/nea/ensemble-src/.venv/bin/python` (Python 3.13.15) from this
worktree's cwd. Binary evidence (the 21 PNG captures + their drivers +
JSON sidecars) lives in
`.agents/shared/planning/metrics-unification/fe-t1-build/pixels/` —
COMMITTED, by explicit leader override of the "binary evidence stays out
of the repo" convention, because the council reviews consume it. Every
OTHER lane artifact was staged under `/tmp/fe-t1-*` at run time and was
SWEPT after the lane (see §9.10, "W-1 remediation"); the historical
`/tmp/...` log paths quoted below are command transcripts, not live files.

---

## 9.1 — The pre-flight fence (P1–P9)

| # | Assertion | Observed | Verdict |
|---|---|---|---|
| P1 | worktree `.env` is the dev fence | `ENSEMBLE_SELF_ENV=dev`, `POSTGRES_HOST=127.0.0.1`, `POSTGRES_PORT=5432`, `POSTGRES_DB=ensemble_dev`, `POSTGRES_USER=ensemble_dev_app`, `POSTGRES_PASSWORD` present (1 line), **`PORT` line count = 0** | PASS |
| P2 | loaded `POSTGRES_DB` | boot script: `POSTGRES_DB=ensemble_dev @ 127.0.0.1:5432 user=ensemble_dev_app`; `!= ensemble_prod` asserted (exit-guarded) | PASS |
| P3 | JSON-path pin (after P4, re-run after boot) | `jq -e '.database == "postgres" and .postgres.db == "ensemble_dev"'` → `true` (rc 0), all three times (post-P4, post-mint, post-boot) | PASS |
| P4 | scratch dir + EXACT pre-seed | `/tmp/fe-t1-dev-data/ensemble.json` = `{"database":"postgres","postgres":{"host":"127.0.0.1","port":5432,"db":"ensemble_dev","user":"ensemble_dev_app","password":"<dev .env password>"}}` (chmod 600) | PASS |
| P5 | sweep guard pinned | `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` exported in both boot scripts (never `env -u`) | PASS |
| P6 | scrub per variable | by name: `POSTGRES_DB/_HOST/_PASSWORD/_PORT/_USER`, the `PG*` family (`PGPASSWORD/_USER/_HOST/_PORT/_DATABASE/_SSLMODE/_CONNECT_TIMEOUT/_APPNAME`), `BLUEPRINT_EMBEDDING_*` (4), the `*_API_KEY` family (`EMBEDDING/_LIGHTRAG/_OPENAI/_PLANE_MCP/_SKILL_EVOLUTION_EMBEDDING`), plus `PORT`/`ENSEMBLE_SELF_ENV`. Ambient poisons CONFIRMED present pre-scrub: `POSTGRES_DB=ensemble_prod`, `POSTGRES_HOST=10.44.0.2`, `PORT=9797`, `ENSEMBLE_SELF_ENV=live`, `RAG_IS_REQUIRED=true` | PASS |
| P7 | effective port | `PORT=8079` re-exported UNCONDITIONALLY after the source (`== BOOT SEQ (4)` log line); `/livez` answered `{"status":"alive","uptime_seconds":…,"version":"0.28.9"}` on `127.0.0.1:8079` | PASS |
| P8a | config.yaml stanzas | `grep -ic "postgres\|database" config.yaml` → **0**; port stanza `daemon.port: ${PORT:-8088}` (lines 111–113) resolves to **8079** with PORT exported | PASS |
| P8b | the daemon's actual DB from the STARTUP LOG | `Loaded ensemble config: database=postgres` (ensemble_config.py:75) + `Creating PostgreSQL engine: 127.0.0.1:5432/ensemble_dev` (repositories/factory) — verbatim in the boot log; NO endpoint added (W1) | PASS |

**The three C-4 falsification checks:**
1. `env | grep POSTGRES` BEFORE the `.env` load → **0 hits** (boot log `== BOOT SEQ (1): scrubbed. POSTGRES_ hits=0 PORT=unset SELF_ENV=unset RAG_IS_REQUIRED=unset`).
2. `No PostgreSQL ENV vars detected, defaulting to sqlite` → **0 hits** in the boot log.
3. `cat $ENSEMBLE_DATA_DIR/ensemble.json` → `.database == "postgres"`, `.postgres.db == "ensemble_dev"` (P3 re-run after the boot: `true`).

**Disclosed deviation (P6 extended):** the ambient shell also carries
`RAG_IS_REQUIRED=true`; the first boot attempt aborted at the RAG auto-test
(`RAGRequiredError: … No credentials provided`) because a source-mode dev shell
has no LightRAG credentials. `RAG_IS_REQUIRED` was added to the per-variable
scrub — the same env-poison family the fence defends against (the v0.28.6
routed note "source-mode dev boots abort on ambient RAG_IS_REQUIRED=1"). The
failed attempt's log is preserved at `/tmp/fe-t1-daemon-auth-on.log.attempt1`
(its DB read-back lines already showed `database=postgres` + the
`ensemble_dev` engine DSN, so the fence logic was validated on the failed
attempt too).

## 9.3a — Auth env export + the fenced mint (B1)

**(a) auth env:** exported in the scrubbed shell BEFORE the boot:
`ENSEMBLE_AUTH_ENABLED=true`, `ENSEMBLE_AUTH_CLIENT_ID=dev-placeholder-client`,
`ENSEMBLE_AUTH_CLIENT_SECRET=dev-placeholder-secret`,
`ENSEMBLE_AUTH_ISSUER=https://dev-idp.invalid/` (https placeholder — the OIDC
login flow is NOT exercised; the bearer lane verifies locally),
`ENSEMBLE_AUTH_ALLOWED_ORIGINS=http://127.0.0.1:8079`. `DISCOVERY_URL`
derives from the issuer. `config.yaml` and the worktree `.env`: **zero diffs**
(W-12: `git status --short config.yaml .env` empty).

**(b)+(c) both-inputs proof (B1b):** pre-source `POSTGRES_*` hits = 0 (C4-i);
post-source P2 pinned the loaded values to the dev fence; P3 re-asserted the
scratch `ensemble.json` pin; then the CLI's own resolution printed:

```
== B1b RESOLVED_DSN: postgresql+psycopg://ensemble_dev_app:***@127.0.0.1:5432/ensemble_dev?application_name=ensemble-manager&connect_timeout=10
== B1b PASS: resolved database is ensemble_dev
```

**(d) the A2 live fence (fail-loud reader; a failed read is never "0 rows"):**

| observation | ensemble_prod total | label-scoped `fe-t1-operator` |
|---|---|---|
| BEFORE | 1 | 0 |
| AFTER | 1 | 0 |
| MID (pre-teardown) | 1 | 0 |
| TEARDOWN | 1 | 0 |

Totals identical across all four observations (0 delta); our label NEVER
appeared on the live DB. SELECTs: `SELECT count(*) FROM auth_bearer_tokens;`
and `SELECT count(*) FROM auth_bearer_tokens WHERE label = 'fe-t1-operator';`
— both rc=0, both numeric-verified, every time.

**(e) the mint:** `/home/nea/ensemble-src/.venv/bin/python -m daemon.auth.cli
issue-token --label fe-t1-operator --scopes "metrics:read"` (the narrower
grant; `maintenance:read` also confers the destructive checkpoint-cleanup
capability). Plaintext printed ONCE, captured to `/tmp/fe-t1-token.out`
(0600) — **never entered a repo file**.

**(f) dev read-back (B1c):** from `ensemble_dev`:
`fe-t1-operator | metrics:read | active | created 2026-11-04T02:30:22Z` —
the row landed in the dev DB.

## 9.2 — The boot (C-4 ordered sequence)

Command: `bash /tmp/fe-t1-boot-auth-on.sh` → `exec
/home/nea/ensemble-src/.venv/bin/python -m daemon` (pinned interpreter, the
`__main__` path so preflight + boot sweep run; NOT dev.sh). Boot log:

```
== BOOT SEQ (1): scrubbed. POSTGRES_ hits=0 PORT=unset SELF_ENV=unset RAG_IS_REQUIRED=unset
== BOOT SEQ (2,3): .env sourced; P2 PASS (POSTGRES_DB=ensemble_dev @ 127.0.0.1:5432 user=ensemble_dev_app)
== BOOT SEQ (4): PORT re-exported unconditionally -> PORT=8079 (DAEMON_PORT=8079 is inert, A-6)
== BOOT SEQ (5): auth env exported (ENABLED/CLIENT_ID/CLIENT_SECRET/ISSUER/ALLOWED_ORIGINS)
== P5 pinned (sweep=0); P3 PASS for /tmp/fe-t1-dev-data/ensemble.json
== BOOT SEQ (6): exec /home/nea/ensemble-src/.venv/bin/python -m daemon
02:33:21 - daemon.api - INFO - Starting Ensemble v0.28.9        ← the boot banner
02:33:22 - daemon.ensemble_config - INFO - Loaded ensemble config: database=postgres
02:33:23 - daemon.repositories.factory - INFO - Creating PostgreSQL engine: 127.0.0.1:5432/ensemble_dev
```

`/livez` on 8079: `200 {"status":"alive","uptime_seconds":6.1,"version":"0.28.9"}`
(~12 s after launch). Auth-OFF restart followed by its own `/livez`
re-assertion: `200`, banner `03:04:25 Starting Ensemble v0.28.9`, same
`ensemble_dev` engine line.

## 9.3b — Leg L1 (data-bearing), all GETs with `Authorization: Bearer …`

| check | command | observed |
|---|---|---|
| families | `GET /api/metrics/families` | **200**, `registry_read_ok: true`, **34 registered families**, availability live/dark per family; **ALL 12 tab-bound families present** (F9,F1,F3 · F4,F5,F6 · F10,F12 · F13,F11 · F8 · F17) — threshold ≥1 far exceeded |
| snapshot | `GET /api/metrics/snapshot?family=F9` | **200**, **13 series** (threshold ≥1) |
| resources | `GET /api/resources/live` | **200**, `daemon.rss_mb = 296.46` (non-zero; the field nests under `.daemon`) |

A 200-with-empty-catalog did NOT occur. (First curl attempt without the
bearer on `/api/resources/*` answered `302 → /api/auth/login` — with auth ON
even the fail-open-scope door requires authentication; the browser presents
the bearer via `extraHTTPHeaders`, which is exactly what the pixel runs did.)

## 9.4 — Leg L1b (auth ON, no credential)

API: `GET /api/metrics/families` with `Accept: application/json` (the Angular
HttpClient shape) → **401** + body
`{"error": "AUTH_REQUIRED", "message": "Authentication required"}` +
`www-authenticate: Bearer realm="ensemble"` (the FR-24 machine contract;
`Accept: */*` browser navigations get the 302 login bounce instead).
Rendered: a no-bearer Playwright context on `/metrics?tab=runtime` → the
sign-in panel — `metrics-door-state-cause` = "The metrics door requires
authentication — this session is not signed in.", action = "Sign in, or
present a token that is scoped for the metrics read.", **0 Retry buttons**,
no `unexpected`. Screenshot: `.agents/shared/planning/metrics-unification/fe-t1-build/pixels/l1b-signin-panel.png`.

## 9.5 / 9.7b — Leg L2/L4 (auth OFF)

Restarted with the four auth vars unset (`/tmp/fe-t1-boot-auth-off.sh`); the
yaml's `enabled: false` governs. `/livez` re-asserted 200 after the restart.

- `GET /api/metrics/families` (no credential) → **503**
  `{"detail":{"error":"AUTH_NOT_CONFIGURED","message":"Auth not configured (fail-closed)"}}`
- `GET /api/resources/live` → **200** (the fail-open resources door stays lit)

PX-8 (both viewports, 5/5 PASS):

```
PASS PX-8@1600: cause_panels=1 (no duplicate banner) cause_named=true
  runbook_pointer=true (docs/runbooks/oauth2-auth.md) retry_buttons=0
  chip=["metrics door: fail-closed"] chip_green=false central_dark=true
PASS PX-8@900:  (identical verdicts at 900×1000)
PASS PX-8-900px-collision: banner horizontal overflow: none
PASS L2/L4-tab1-lit@1600: live_strip=1 timeline=1 auth_panels_in_tab1_body=0 rss shown
PASS L2/L4-tab1-lit@900:  (identical verdicts)
```

Panel copy (the SSOT): cause "The metrics door is fail-closed — auth is not
configured on the daemon.", action "An operator must configure auth on the
daemon and restart it.", pointer `docs/runbooks/oauth2-auth.md`,
`retryable: false`. Tab 1's body stays lit (the page-scoped catalog panel
sits OUTSIDE `.metrics-tabs-host` by design — AM-1: the state is per SOURCE).
Screenshots: `px08-auth-not-configured-{1600,900}x1000.png`,
`l2l4-tab1-still-lit-{1600,900}x1000.png`.

## 9.6 — Leg L3 (mixed-unit correctness)

`[data-test="timeline-panel-memory"]`, `…-cpu`, `…-count` — three DISTINCT
unit-class panels, each with its OWN canvas (1/1/1; no shared axis element).
The count panel appears when a count-class series is toggled on
(`series-toggle-threads`; default-on is memory+cpu — the toggle path is part
of §10 criterion 5). Unit labels are canvas-drawn by Chart.js (the pixel is
the visual proof of MB / % / count on separate y-axes); the DOM geometry
proof is the three independent per-panel canvases.
Screenshot: `px10-mixed-units.png`.

## 9.7a — the pixel list (auth ON; 16/16 assertions PASS)

Driver: `.agents/shared/planning/metrics-unification/fe-t1-build/pixels/capture-9.7a.js` (canonical shape; Playwright from
`frontend/node_modules`, chromium `--no-sandbox`, domcontentloaded 30 s →
networkidle best-effort 15 s → 3 s settle), viewport 1600×1000.

| item | verdict (observed) | file |
|---|---|---|
| PX-1 tab 1 WITH DATA | live_strip=1 timeline=1 anomalies_panel=1 anomaly_rows>0 (`[data-test^="anomaly-row-"]`), disclosure text present | px01-tab1-resource.png |
| PX-2 runtime | panels>0, family chips, no no-catalog | px02-tab2-runtime.png |
| PX-3 db-pool | idem | px03-tab3-dbpool.png |
| PX-4 jobs | idem | px04-tab4-jobs.png |
| PX-5 instances | 6 panels, 2 family chips | px05-tab5-instances.png |
| PX-6 checkpoint | 1 panel + chip | px06-tab6-checkpoint.png |
| PX-7 mcp | stat-strip-only tab (F17 is stat-primary): strip=1 chip=1, no catalog error | px07-tab7-mcp.png |
| PX-9 redirect | `/resources?window=24h&x=1` → `/metrics?window=24h&x=1&tab=resource`; parsed params all preserved (window=24h, x=1, tab=resource) | px09-resources-redirect.png |
| PX-10 mixed units | 3 distinct unit panels, each own canvas | px10-mixed-units.png |
| PX-11 baseline | runtime: toggle enabled, checked → the fidelity disclosure renders: "the baseline is downsampled over the whole fetched window — a bucket can straddle the split, the older half sits at the ring's eviction edge, and the resolution halves"; custom tab: toggle DISABLED with reason "the resources door serves a fixed 15m/1h/24h/7d vocabulary with no previous-window offset" | px11a-runtime-baseline-on.png, px11b-custom-tab-toggle-disabled.png |
| PX-12 range switch | central options [15m,30m,1h,2h,**24h(disabled)**,**7d(disabled)**] — the option set is visible and ENFORCED per-option; custom tab has its own labelled control + note | px12a-runtime-range.png, px12b-custom-range.png |
| PX-13 markers + gaps | anomaly rows carry severity + peak; the marker label format is `kind · shape · severity N · series … · peak …`; gap chips (when present) say "DATA GAP"; the only "restart" text on tab 1 is the HONEST journal disclosure "Process-local journal — resets on daemon restart and keeps only the last 256 entries; it never spans a restart" — not a gap mislabel | px13-anomaly-markers.png |
| PX-14 dark listing | dark entries render with reasons: "F1 dark — the wedge capture is not active", "F3 dark — the wedge capture is not active"; live entries also listed | px14-dark-families.png |

**API census (both pixel runs combined): 790 `/api/` responses,
non-GET = 0, `:9797` hits = 0.** Console errors 234 (pre-existing noise;
0 pageerrors). Evidence JSONs:
`capture-9.7a-evidence.json`, `capture-9.7b-evidence.json`.

## 9.8 — Bundle guard (B1–B8)

Fresh production build: `cd frontend &&
./node_modules/.bin/ng build --configuration production` → rc=0 (log
`/tmp/fe-t1-p9-prod.log`); measurement
`node ../.agents/…/bundle-baseline.measure.js dist/frontend/browser`.

| guard | threshold (AS WRITTEN) | measured | verdict |
|---|---|---|---|
| B1 initial-total delta | \|Δ\| ≤ 2,048 B vs 5,909,018 B (the artifact) | **5,907,717 B** → Δ **−1,301 B**; headroom **92,283 B** | GREEN |
| B2 chart.js marker in initial chunks | 0 hits across the **36-file** initial set (35 JS + 1 CSS) | **0 hits** | GREEN |
| B3 import shape + site (statement-anchored) | 0 static `from 'chart.js'`; every site under `src/app/pages/metrics/**`; runtime form = dynamic | **0 static statements** (comment lines excluded per W6); 2 dynamic sites, both in `charts/chart-panel.component.ts` (line 164 `type ChartJsModule = typeof import('chart.js')` — type-erased; line 477 `await import('chart.js')` — the runtime form) | GREEN |
| B4a new chart chunk min+gz | ≤ 65,536 B gz | `chunk-TXUCW6LI.js` = **206,857 B min / 71,054 B gz** → **over by 5,518 B gz** | **FAIL AS WRITTEN — caller adjudication** (recorded; NO code or threshold changed) |
| B4b route fetch set growth (lazy-only basis, per the artifact's implementation-time ruling) | ≤ 65,536 B growth vs the 72,708 B lazy-only baseline | lazy-only **165,185 B** → growth **+92,477 B** (over by 26,941); total 682,399 vs 496,338 = +186,061 (transparency) | **FAIL AS WRITTEN — caller adjudication** |
| B5 anyComponentStyle | each new component < 8 kB; the 24 kB cap is a hard error | new scss (raw): `timeline-tab.component.scss` 6,751 B · `live-tab.component.scss` 4,771 B · `metrics.component.scss` 3,535 B; the warning set contains ZERO metrics-page entries → every compiled component style under 8 kB | GREEN |
| B6 warning SET difference vs the 9-path baseline | 0 added / 0 removed (never a count ceiling) | sorted `diff` of the exact `scss exceeded maximum budget` lines → **empty diff (set equal, 0/0)**; the pre-existing `bundle initial …` warning also present | GREEN |
| B7 packs | 2/2 green + 161/161 | see 9.9/9.10 | GREEN |
| B8 the guard read the named artifact | the guards read `bundle-baseline.json` | B1/B4 thresholds taken from the artifact's fields (5,909,018 / 72,708 / 496,338 / 65,536); a fresh measurement reproduced the tool's own output shape with `route_chunk_locator: "route-key"` | GREEN |

**B4 composition analysis (for the adjudication):** the lazy-only delta is
ALMOST EXACTLY the route chunk's own growth — `chunk-4ECAPKNS.js` (the
`/metrics` route chunk) is **120,154 B vs the lane-base 27,672 B (+92,482 B)**
because Phase 5/7 RELOCATED the resources module into the metrics route (the
route now carries the whole tab-1 implementation). The two legacy lazy-only
deps are byte-stable (43,683 + 1,348 ≈ the baseline's 43,688 + 1,348, −5 B
minifier drift). The chart vendor chunk is NOT in the route fetch set at all
(the D-1-mandated dynamic barrel import keeps it a separate on-demand chunk —
B3's static-leak case was mutation-proven in Phase 4). The shared portion grew
423,630 → 517,214 (+93,584; the shared Material chunk — also the relocation's
own content). **No vendor leak anywhere; the growth is the lane's OWN
deliverable (the relocated module) — the 64 KiB thresholds, written for a
pure charting add-on, do not model a module relocation.** Per instructions
both numbers are recorded, the gate outcome is stated per the threshold AS
WRITTEN (FAIL), and nothing was contorted.

### The A6 tool fix (in-mandate, additive)

`bundle-baseline.measure.js`'s `pre_retirement_resources_route_chunk` keyed on
the legacy `anomaly-marker` content marker — which after the Phase-5
relocation LIVES IN THE METRICS ROUTE CHUNK. Before/after on the same dist:

```
BEFORE-A6 (raw legacy lookup):
  route-key resolves to:      chunk-4ECAPKNS.js (exactly one: true)
  legacy anomaly-marker:      chunk-4ECAPKNS.js
  SAME CHUNK (the mis-resolution A6 predicted): true
AFTER-A6 (the fixed tool):
  pre_retirement_resources_route_chunk: null
  pre_retirement_resources_route_chunk_bytes: null
  route_chunk_locator: "route-key"  (still resolves correctly)
```

The fix: when the legacy marker resolves to the SAME chunk as the route key,
the field reports null (absent) — never a mis-resolution. Additive only; no
other field's semantics changed; route-keyed + absent-tolerant + exactly-one
semantics kept.

## 9.9 — Both FE packs

1. `bash test/packs/fe_static_typecheck_build_test.sh` — first run FAIL(DRIFT):
   the pack's Stage-0 `EXPECTED_BRANCH` defaults to `feature/mission-class`
   (a FOREIGN lane's default; the variable is env-parameterized by design).
   Re-run with `EXPECTED_BRANCH=feature/fe-metrics-dashboard`: **RESULT: PASS**
   (Stage 1 `tsc --noEmit` exit 0 through `frontend/`'s local devDependency;
   Stage 2 production build exit 0; SHA bracket d2b4c2505 stable;
   SCSS_WARNING_COUNT=17 printed verbatim as adjudication data, not a gate).
2. `bash test/packs/metrics_fe_dual_side_test.sh` — same Stage-0 default
   (`feature/central-metrics-v1`); re-run with this lane's branch:
   **D1 (lane-owned specs): 224/224 PASS · D2 declared: 131/131 PASS ·
   Leg 7 expected-dependency-set: `ok: true` BY NAME (dependencies: exactly
   `chart.js`; devDependencies: exactly `jest-canvas-mock`) · D2 derived
   whole-FE sweep: 1832 passed / 2 failed.** The 2 failures are BOTH
   `src/app/pages/settings/settings.component.spec.ts › Timezone preference
   (user-timezone-setting)` ("renders the timezone section with the native
   picker when Intl.supportedValuesOf is available" + "uses the API list when
   the GET /api/settings/timezones response is non-empty") — the
   **base-proven date-dependent timezone-family reds** (proven at base in
   phase1-evidence.md §321–455; part of the known 9-red/4-suite base debt
   with schedule-create-dialog and jobs-filter-state ×2, which this sweep's
   composition did not include). **ROUTED as base debt — never "fixed" in
   this lane.** Pack verdict: PASS-with-preexisting.

## 9.10 — Prompt-artifact baseline

`/home/nea/ensemble-src/.venv/bin/python
scripts/protection/generate_prompt_artifact_baseline.py --check` →
`PROMPT-BASELINE: OK — 161 versioned artifact(s) match
.prompt-artifacts.sha256` (rc 0). `git diff --stat HEAD -- agents/` → empty;
`git status --short agents/` → empty.

## 9.11 — Close-out

**The routed-out register — 15/15, each with an owner (plan-overview §7):**

| # | Routed item | Owner |
|---|---|---|
| a | Enable auth on live (the operator recipe, §9) | USER (morning item) |
| b | Per-instance CPU/mem through the central door | metrics lane (own commission) |
| c | LLM tokens / LLM latency / source-breaker families | metrics lane (Tier-3 waves) |
| d | Durable anomaly/restart events spanning restarts | metrics lane (small commission) |
| e | Mermaid's 972 kB gz initial-bundle elephant | FE debt lane |
| f | T1.5 backend `end_offset_s` / `compare` params | metrics lane (small, optional) |
| g | Retire `/api/resources/control-capabilities` + `POST /api/resources/profile` | backend hygiene commission |
| h | F1/F3/F6 false-dark fixes (R-i, R-ii) | metrics lane (already routed) |
| i | F0.3 spine-health `TypeError` | metrics lane (already routed) |
| j | Central windows > 2 h | metrics lane (unpaved) |
| k | BE-served manifest (shape A) | metrics lane (T2+) |
| l | ECharts / ApexCharts / Gridster2 | deferred with triggers |
| m | The per-instance top-N list on tab 1 | follow-up commission |
| n | The `/resources` gate pack | this lane, Phase 7 (retired with the route) |
| o | `docs/runbooks/oauth2-auth.md` §1b env gap (omits `ENSEMBLE_AUTH_ISSUER`) | auth-docs owner |

**The operator recipe is handed over, NOT executed.** §9 of
`plan-overview.md` IS the canonical source; it is quoted verbatim in this
lane's final report. **0 live config writes; 0 live daemon requests** (the
API census: 0 × `:9797` across 790 responses; every curl targeted
`127.0.0.1:8079`; no tracked config file was edited).

**The live fence (AM-29 wording):**
- symlink `current` → `releases/v0.28.9` — unchanged pre/post;
- `state.json` sha256 `e7c5db34fbd00faa…91310dd98` — **byte-identical**
  mid vs post;
- live uptime **monotone** across the phase (41964 s → 41984 s → 42038 s, no
  restart);
- the write-detecting DB fence: ensemble_prod bearer totals **1/1/1/1**
  (BEFORE/AFTER/MID/TEARDOWN — 0 delta), label-scoped **0 at every
  observation**; all eight SELECTs succeeded (rc 0, numeric-verified) — an
  unreadable fence would have STOPPED the lane.

**The dev-DB teardown (W10):** the auth-OFF daemon killed (SIGTERM; `/livez`
confirmed down); then
`DELETE FROM auth_bearer_tokens WHERE label = 'fe-t1-operator';` in
`ensemble_dev` → **DELETE 1** (exactly the minted row); the table still
exists (pg_tables count 1); `SELECT current_database()` → `ensemble_dev` —
**ensemble_dev NOT dropped**; live counts re-asserted (TEARDOWN row above).
The scratch data dir `/tmp/fe-t1-dev-data` removed.

**Disclosure (dev-DB row census drift, NOT a lane write):** ensemble_dev's
pre-existing bearer rows went 4 (pre-mint) → 3 (post-teardown). My
statements were exactly INSERT 1 (the mint) + DELETE 1 (the teardown). The
two additional rows were removed by the DAEMON's own expired-token sweep
(`sweep_expired_states` / `_sweep_expired_batched`,
daemon/repositories/auth_session/repository.py) during the two dev boots —
the task context's own note ("THIS PHASE BOOTS A DAEMON AND WRITES TO A
DATABASE") covers daemon-inherent maintenance on the shared dev DB. The
three surviving rows: `x` (2026-10-18, other work), `fe-metrics-e2e-scoped`
and `fe-metrics-e2e-noscope` (this lane's OWN Phase-2/3 e2e tokens — left by
those phases' teardown decisions, outside 9.11's scope).

**The E-2 release narrative (§9.1) and the disclosure set (§0.4) ride the
staged-release disclosures** — reproduced in the final report.

## E-1 re-derivation (this phase's gated figures)

| figure | value | command + output |
|---|---|---|
| initial total | 5,907,717 B | `node …/bundle-baseline.measure.js dist/frontend/browser` → `initial_total_bytes: 5907717` |
| headroom | 92,283 B | same output `headroom_bytes` |
| route chunk | 120,154 B (`chunk-4ECAPKNS.js`) | same output `metrics_route_chunk_bytes` |
| route fetch set | 682,399 B total / **165,185 B lazy-only** | same output `metrics_route_fetch_set_bytes` / `…_lazy_only_bytes` |
| new chart chunk | 206,857 B min / **71,054 B gz** | `gzip -9 -c dist/frontend/browser/chunk-TXUCW6LI.js | wc -c` → 71054 |
| warning set | 9 paths, set-equal to baseline | `grep -E "scss exceeded maximum budget" <log>` sorted, `diff` vs baseline → empty |
| pre-retirement /resources chunk | **null (absent)** post-Phase-7 | the fixed tool's `pre_retirement_resources_route_chunk` |

---

## THE OPERATOR AUTH RECIPE — quoted VERBATIM from plan-overview.md §9 (handed over, NEVER executed)

> **Steps (live install):**
>
> 1. **Register an OAuth2/OIDC client at the IdP** — a *confidential* client,
>    Authorization-Code + PKCE. Register the callback URI **byte-for-byte** as
>    the daemon derives it: `https://<daemon-host>/api/auth/callback` (derive
>    FIRST per runbook §1c, then register that exact value). Issue a client secret.
> 2. **Set the daemon environment** — **six** vars, and the sixth is the one the
>    cited runbook omits (B4):
>    - `ENSEMBLE_AUTH_ENABLED=true`
>    - `ENSEMBLE_AUTH_CLIENT_ID=…`
>    - `ENSEMBLE_AUTH_CLIENT_SECRET=…` (never in the repo; never logged)
>    - **`ENSEMBLE_AUTH_ISSUER=https://<idp>/`** — **REQUIRED and NOT derivable
>      from the discovery URL**: `AuthConfig.issuer` has **no default**
>      (`daemon/config.py:3736-3741`) and it is the issuer the discovery URL is
>      derived FROM, not the reverse (`discovery_url`'s description:
>      *"Default derives as `<issuer>/.well-known/openid-configuration"`"* —
>      `daemon/config.py:3742-3748`); `daemon/config.py:3953-3954` appends
>      `"issuer"` to the `missing` list, so without it the daemon **refuses to
>      boot with exit 78**.
>    - `ENSEMBLE_AUTH_DISCOVERY_URL=https://<idp>/.well-known/openid-configuration`
>      — **origin rule**: the discovery document's origin must equal the issuer's
>      origin (`daemon/config.py:3984-3988` raises otherwise); a same-origin /
>      different-path discovery URL stays legal.
>    - `ENSEMBLE_AUTH_ALLOWED_ORIGINS=https://<daemon-host>` (**REQUIRED** — with
>      auth ON the CORS posture must replace wildcard+credentials; an empty list
>      refuses the boot with exit 78).
>
>    **The cited runbook is env-INCOMPLETE (B4, disclosed — it lives outside the
>    plan dir, so this lane records the gap rather than editing it):**
>    `docs/runbooks/oauth2-auth.md` §1b lists five vars and omits
>    `ENSEMBLE_AUTH_ISSUER` … **This §9 is the CANONICAL operator-recipe source** …
>    the runbook's gap is a routed documentation item (owner: the auth-docs owner).
> 3. **Mint the operator token** … the three query routes take the **ANY-OF** set
>    `maintenance:read` OR `metrics:read` (`daemon/routers/metrics.py:56-63`).
>    **Use the narrower `metrics:read`** (B1e, least privilege) … for a machine
>    reader, mint a static bearer with the **pinned interpreter** (W9):
>    `/home/nea/ensemble-src/.venv/bin/python -m daemon.auth.cli issue-token
>    --label operator --scopes "metrics:read"` (the plaintext prints ONCE; the row
>    stores only a SHA-256). **The mint IS a database write** — run it against the
>    intended database.
> 4. **RESTART implication (explicit):** auth has **no live toggle in v1** — the
>    `auth.enabled` flip and the new env vars require a **daemon restart**
>    (`config.yaml:129-152`). The restart is the moment the central door flips
>    from 503 `AUTH_NOT_CONFIGURED` to serving. Plan the restart window.
> 5. **Verify** — `GET /api/metrics/families` returns 200 with a bearer; without
>    one it returns **401** (`AUTH_REQUIRED`). With auth OFF the same URL returns
>    503 `AUTH_NOT_CONFIGURED`.
> 6. **Also decide (user-owned, out of the lane):** the `/api/resources/*` door
>    is fail-**open** by design (scope manifest `*`) while the metrics door is
>    fail-**closed** — merging the surfaces under one page forces a conscious
>    choice about that posture.

(§9.0's runbook semantics — the bootstrap one-shot lane, the integer-seconds
`--ttl`, the mandatory allowed-origins, the cold Host-recorder trap, and the
MERGE-WITH-AUTH-OFF posture — carry in plan-overview.md §9.0 verbatim; this
evidence defers to that section rather than duplicating it.)

---

## POST-RULING RIDER (2026-11-04) — leader ruling IMPL-24 (the B4 re-baseline)

The leader re-baselined B4 on the measured reality (B4a 65,536 → 82,000 B min+gz;
B4b → 185,000 B lazy-only on the POST-RELOCATION basis) with the supersession
recorded in `bundle-baseline.json` guards.B4, `plan-overview.md` §0.2 (G4b, G9) +
§6.3 (row B4), and `decisions.md` IMPL-24. Full before/after tables, the planted
static-edge proof (plant → exit 4 RED → restore → exit 0 green), and the re-run
guard outputs live in the lane's final report and in the artifact itself. The tool
gained the load-bearing VENDOR-STATIC-EDGE INVARIANT (vendor bytes in ANY static
fetch set == 0; identification = the route-keyed route chunk's dynamic-import
targets, exactly one on this dist, all-targets coverage as the fallback).

## A PLAN-REFERENCED-ARTIFACT GAP — the data-end marker (recorded as its own defect class)

**The gap:** the plan called the data-end overlay a "Phase 4 contract" — Phase 8's
task 8.10 (`The data-end marker … Depends On: Phase 4, Phase 6` in
`phase9-plan.md`'s dependency framing) presumes a Phase-4-frozen contract and
capability for the data-end marker. **No such contract or capability existed at the
Phase-4 freeze** — the plan's Phase 4 scoped the chart-panel wrapper, the renderer
registry, the port, and the house SVG renderer; a data-end marker is named in NO
Phase-4 task, no Phase-4 exit bullet, and no IMPL ruling before Phase 8.
**Phase 8 therefore built it from scratch as a house plugin**
(`charts/data-end-overlay-plugin.ts` + `data-end-overlay-plugin.spec.ts`, a
Chart.js plugin drawn on the panel canvas), not as a port of a planned contract.

**Why this is recorded as a DISTINCT defect class:** a stale number (the class
IMPL-24 closes) is a measured figure that reality outran — the artifact existed and
was corrected. A **plan-referenced artifact that does not exist** is different: the
plan's dependency graph cites a deliverable that was never planned, so the citing
phase's work could not "implement the contract" — it had to invent one. The
dependency edge was unverifiable at planning time and silently dangling at
execution time. The cost was real (Phase 8 designed the marker's semantics — the
end-of-data tick and its honesty rule — with no frozen contract to conform to), and
the risk is real for any future reader who assumes a Phase-4 authority exists.
**Disposition:** the shipped plugin is the de-facto contract now (its spec is the
frozen surface); this record prevents the phantom-authority citation from
propagating — future plans should verify that a referenced artifact EXISTS at its
cited freeze, not merely that it is named.

---

## 9.10 — W-1 remediation (council fold: prod credential at rest in /tmp)

**The finding (coding-model council, W-1).** The lane's live-fence helper
`/tmp/fe-t1-fence_obs.sh` hard-coded the PRODUCTION PostgreSQL credential
in plaintext, in a world-readable (`0755`) file outside the repo. The repo
itself was clean of it (`git grep` finds only two pre-existing Sept-2026
files this lane never touched), the minted bearer landed only in a `0600`
`/tmp/fe-t1-token.out`, and the mint/teardown ran against `ensemble_dev`
— but the helper parked the prod password at rest for the life of the box.
The credential is referred to as `<the prod password>` below and appears
in NO artifact this fold writes. **No credential was rotated** — rotation
is user-owned.

### (a) The CORRECTED fence pattern (credential SOURCED, never inlined)

```bash
#!/usr/bin/env bash
# The live-fence probe — CORRECTED SHAPE.
#
#1 the credential has exactly ONE at-rest home: the install's own .env
#   (mode 0600, install-owned). Never inline it into a lane file.
#2 it reaches psql through THIS process's environment for ONE command.
#   It is never echoed, never written to a log, never put on a CLI argv
#   (argv is world-readable via /proc).
#3 the probe is SELECT-only.
set -euo pipefail

INSTALL_ENV=/home/nea/agents-ensemble/.env      # mode 0600
# Verified in this fold (values never printed): the file carries exactly
# one POSTGRES_HOST / POSTGRES_PORT / POSTGRES_DB / POSTGRES_USER /
# POSTGRES_PASSWORD each — i.e. the five values the old helper inlined
# (10.44.0.2 : 5432 / ensemble_prod / ensemble), so sourcing it removes
# the hardcoded copy outright rather than relocating it.

# Map the sourced values to libpq's own variable names in a SUBSHELL, so
# nothing leaks into the calling shell and nothing is echoed.
(
  set -a
  # shellcheck disable=SC1090
  . "$INSTALL_ENV"
  set +a
  PGPASSWORD="$POSTGRES_PASSWORD" \
  psql -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" \
       -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 -tAc \
       "select count(*) from information_schema.tables where table_schema='public'"
)
# <the prod password> appears NOWHERE in this script's text. The only
# at-rest copies are the install's own .env (0600) and the kernel's
# per-process environment.
```

Not re-executed in this fold: the remediation is the SHAPE, and the lane
takes no further live contact. The old pattern's other defect is worth
naming — it also hard-coded the host, so a stale copy would silently
outlive a DB move; sourcing cannot drift.

### (b) Post-lane sweep — inventory, then remove

Inventory taken BEFORE any deletion (`ls -la` + `find -type f`, no process
holding them — `lsof /tmp/fe-t1-*` empty):

| Class | Count | Disposition |
|---|---|---|
| `/tmp/fe-t1-pixels/` (21 PNG + 2 capture drivers + 2 JSON sidecars) | 25 files | **RELOCATED** → `pixels/` (committed — leader override; see §9.10(c)) |
| Credential-bearing: `fe-t1-fence_obs.sh` (prod password ×2), `fe-t1-token.out` (`0600`, the minted bearer) | 2 files | **REMOVED** |
| Build logs (`fe-t1-build-*` ×3, `fe-t1-p2/p3/p4/p5/p7/p9-prod.log` ×7, `fe-t1-p4-planted-eager.log`) | 11 files | **REMOVED** |
| Jest / pack logs (`fe-t1-p3-jest-*` ×2, `fe-t1-pack-*` ×3, `fe-t1-p4-base-jest.txt`) | 6 files | **REMOVED** |
| Daemon logs + boot scripts (`fe-t1-daemon-*`, `fe-t1-boot-auth-{on,off}.sh`) | 6 files | **REMOVED** |
| Mint helper + stderr (`fe-t1-mint.sh`, `fe-t1-mint.stderr`) | 2 files | **REMOVED** (it only `unset`s `PGPASSWORD`; no secret in it — removed as a no-longer-needed artifact) |
| Fence/live probes & JSON captures (`fe-t1-families.json`, `-resources-live.json`, `-snapshot-f9.json`, `-livez*.json`, `-401-*`, `-live-state.sha`, `-fence.log`) | 9 files | **REMOVED** |
| Guard/measure scratch (`fe-t1-measure.js` copy, `fe-t1-p9-measure.json`, `-green.{json,err}`, `-plant.{json,err}`, `-route-chunk.bak`) | 7 files | **REMOVED** (the canonical guard/measure tools live in the repo; see §9.10(c)) |
| Warning/sort scratch (`-baseline-warnings*`, `-p2-warnings.txt`, `-p9-warnings*`, `-p9w.sorted`) | 6 files | **REMOVED** |
| **Total** | **50 top-level entries = 49 files + 1 dir / 74 files / ~3.1 MB** | **25 files relocated · 49 files removed · 0 kept** |

**Nothing was kept**, and nothing needed keeping: no standing process held
any of these files (`lsof` empty before the sweep; the lane's dev servers
are ephemeral and were already down). Post-sweep check: `ls -d
/tmp/fe-t1-*` → no match, and a credential scan over the whole of `/tmp`
for `PROD_PW=` / `PGPASSWORD=<value>` / `postgres://` / `Bearer <token>`
returns zero hits from this lane.

**Credential-scan hits at inventory time** (for the record): `PROD_PW=`
and `PGPASSWORD=` — `fe-t1-fence_obs.sh` only (the 2 real secrets);
`Bearer` — `fe-t1-401-headers.txt` (the `www-authenticate: Bearer` scheme
word, no token) and `pixels/capture-9.7a.js` (a *runtime* read of the mint
path + the `` `Bearer ${bearer}` `` template — no literal token). The
relocated drivers therefore carry NO secret; note that
`capture-9.7a.js:12` reads `/tmp/fe-t1-token.out`, which this sweep
DELETED — to re-run a capture the operator must re-mint (with the
corrected fence shape above) rather than expecting the old path to
resolve. The 21 PNGs and both JSON sidecars are self-contained.

### (c) What moved into the repo, and why

`pixels/` (25 files, 2.3 MB, set sha256 `296f4202…76f0c15`) is committed
**by explicit leader override** of the house convention that binary
evidence stays out of the repo: it is the artifact both council reviews
consumed (and the agentic review's one disclosed limit was that it could
NOT read them from `/tmp` under its write fence). The docs' references
were re-pathed from `/tmp/fe-t1-pixels/...` to the repo-relative
`…/fe-t1-build/pixels/...` (§9.1 intro, §9.4, §9.7a). The canonical guard
and measure tools are NOT in `/tmp` and never were: they live in the repo
as `bundle-guard.js` / `bundle-baseline.measure.js` / `bundle-baseline.json`
beside this document; the `/tmp` copies were scratch.
