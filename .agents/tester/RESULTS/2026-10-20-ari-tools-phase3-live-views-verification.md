# ari-tools Phase 3 — Live views for rich content (`view_write` + FLAGSHIP live board view) — INDEPENDENT VERIFICATION

Date: 2026-10-20
Tester lane: independent verification of the Phase-3 capability (not a re-run of the developer's suite)
Worktree: `/home/nea/ensemble-src-wt-ari-tools` (branch `feature/ari-tools`)
Code range under verification: `39cce206a..a2c33c20` (**9 commits** — git-confirmed); HEAD at verification time `f67232a67` = a **docs-only** follow-up (4 planning files, `-- daemon/ agents/ tests/ frontend/ scripts/` EMPTY ⇒ zero code, no scope violation)
Binding spec: `.agents/shared/planning/ari-tools/phase3-plan.md` + `decisions.md` §C10 (P3-DEV-1/2)

> **VERDICT: 🟡 PASS WITH ONE DEFECT — the Phase-3 capability is verified working end-to-end**
> **(all 9 focus areas PASS); one LOW-severity product defect (D1, percent-encoded path) with a**
> **repro and a recommended pre-merge fix; two spec-bar items disclosed (query count 6 vs the ≤4**
> **brief bar; the seal tripwire's standing RED). ZERO new failures in the scoped surface — every**
> **observed failure proven pre-existing at the base commit. No source changes, no commits by any**
> **verification lane; residue 0.**

> The capability was verified against REAL code on REAL PostgreSQL `ensemble_dev` and a REAL
> filesystem root: the passcode-gated serving e2e, overwrite-without-generation-bump,
> refresh-bump with grant revocation, the full traversal/symlink/cap/atomicity write-safety
> matrix, and the three-layer dual gate with **zero side effects proven by mutation-grade
> positive controls**; the flagship board verified server-rendered-from-DB (freshness, XSS
> parser-level proof, CSP/nonce parity, byte/query caps, truthful pagination, uniform 404,
> mount order, the real-POST T-B7 ladder).

---

## 1. Scope decision

Change set = 27 files, +5559/−66 (5 new daemon modules, 6 edited daemon files, 2 agent meta.json,
config.yaml doc-only, 9 test files, 2 planning docs). Additive surface; **no migration** (delta EMPTY),
no boot path beyond a fail-soft lifespan root injection, no deadlock-sensitive code.

A full-suite run was NOT warranted. Scoped surface = live-views/board (the changed packs) + the
ensure.md Core statics + the registered concurrency pack. The repo-wide pre-existing baseline
(129 failures + 21 errors at the parent commit) is DIFFED against, not chased.

ensure.md Release Gate NOT run (requires booted daemon + real LLM calls; change set touches none of
those paths).

## 2. Method

Four lanes in parallel, one skill each, all on the dev fence:

| Lane | Skill | Scope |
|---|---|---|
| L0 `p3-l0-static` | none (infra) | git/pin-ceremony forensics, frozen seam, migrations, seal tripwire (static), reserved names, `_pin_updated_at` classification |
| L1 `p3-l1-viewwrite` | `integration-test` | `view_write` real e2e, write-path safety, dual gate zero-side-effects + literal-allow bypass |
| L2 `p3-l2-board` | `integration-test` | board page on real DB, CSP/nonce, XSS, byte/query caps, uniform 404, mount order, T-B7 real POST, reserved names, `reports` root out-of-the-box |
| L3 `p3-l3-suites` | `test-pack-execution` | phase-3 suites A/B/C, marker-correct 7-pin invocations, live-views regression + board corpus, baseline diff, ensure.md Core |

**Fences (held by every lane, verified in each lane's audit):**
- DEV fence only: PostgreSQL `ensemble_dev` @ 127.0.0.1:5432 (or unix socket, peer auth); the ambient
  `POSTGRES_*` (→ `ensemble_prod` @ 10.44.0.2) overridden on every DB-touching command;
  `current_database()` asserted in-probe.
- Live install `:9797` / `ensemble_prod` — never contacted, signalled, restarted or killed. Port `8088`
  (ensemble self-system) never touched.
- No HOME-derived / live-install-shaped paths in any fixture (`mktemp -d` / `tmp_path` only; every
  `rm -rf` escape-guarded) — the 2026-10-12 live-install-deletion convention.
- No `git checkout`/`stash`/`reset`/`clean`/`add -A` in the shared worktree; the foreign
  `.agents/shared/planning/designer-agent/install-audit.jsonl` left exactly as found.
- No source modifications, no commits by any lane; every invocation `timeout 300`-wrapped.
- `PYTHONDONTWRITEBYTECODE=1` where relevant; worktree left byte-identical.

---

## 3. Per-lane results

### L0 — static / git / pin-ceremony — ✅ 8/8 PASS, 0 product defects

| item | verdict | evidence |
|---|---|---|
| range census | ✅ | 9 commits `39cce206a..a2c33c20`; HEAD `f67232a6` docs-only (4 planning files, zero code) |
| **pin ceremony** | ✅ | **ONE commit `f585adea8`** carries `view-write` (#11) + `board-link` (#12) + **all SEVEN** exact-set pin files; no other commit in range touches a pin file; trajectory AST-proven 8 → 9 (P1) → 10 (P2) → **12** (P3); the privileged set was never red between commits |
| count pins | ✅ | `test_board_registration.py:185` + `test_self_alarm_registration.py:187` → `len(...) == 12`; AST count of registry = **12** (regex inflates to 22 — comments quote names) |
| frozen seam | ✅ | `git diff -G 'def list_items\|def get_item' 99a28e4f3..a2c33c20 -- daemon/` = **EMPTY**; `board_service.py` occurrences 2@base / 2@HEAD byte-identical; phase-1 files untouched (`--stat` EMPTY) |
| migration delta | ✅ | **EMPTY** (106 files unchanged) — no table, no column |
| seal tripwire (static) | ✅ | 7 stale sealed pins identical at 4 revisions (worktree, 39cce206a, 99a28e4f3, 630cefebb) ⇒ **NEW=[] / RESOLVED=[]**; `git diff --name-only 39cce206a..HEAD -- <35 sealed paths>` = EMPTY; `agents/.prompt-artifacts.sha256` 161/161 OK. Runtime half owned by L3 |
| reserved names + A-39 | ✅ | `RESERVED_ROOT_NAMES = {"public","board"}` (`daemon/services/live_views.py:149`); refusal message (`daemon/config.py:3149-3160`) names the reserved SET generically (A-39); `reports` injection `daemon/api.py:1769-1771` → `inject_reports_live_view_root` (`:449-501`) with fail-soft (`data_dir is None` ⇒ root absent, no boot failure) + operator-honour (`"reports" in roots` ⇒ verbatim) branches |
| `_pin_updated_at` | ✅ | **DIRECT-DB FIXTURE** — raw `UPDATE board_items SET updated_at` at `tests/unit/routers/test_live_views_board.py:1142-1159` (call sites `:1346`, `:1404`); production seam (`board_service.py:196/:307`) untouched by phase 3 |
| worktree hygiene | ✅ | exactly one dirty path (the foreign `install-audit.jsonl`); no untracked files |

Reporting artifacts (NOT code defects): A1 the literal `-G` command is non-empty only because
phase-2 docs quote it (scoped = EMPTY); A4 the older `.agents/tester/` prose says "five pins" — the
authoritative set is SEVEN (P1/P2 added `test_board_registration.py`, `test_self_alarm_registration.py`;
the tree's SAME-PR comment was corrected in `f585adea8`).

### L1 — `view_write` e2e + write safety + dual gate — ✅ 16/16 executed · 1 defect (D1, LOW)

Harness: in-process FastAPI + real `build_router()`; `reports` root minted by the REAL
`inject_reports_live_view_root()` (the production lifespan seam) with `data_dir` = mktemp dir; real
`LiveViewsService` + `ViewPasscodeService` over real PG `ensemble_dev`; real factory
`create_view_write_tools(..., agent_id=...)`. Every probe asserts `current_database()='ensemble_dev'`.

**Focus 1 — e2e (all PASS):** 8-line envelope, first five labels byte-match `view_link`; public URL +
6-char passcode (frozen 31-symbol alphabet); no-grant GET ⇒ 200 interstitial (1805 B, `code` form),
content absent; correct-code POST ⇒ **303** + `view_grant` cookie (HttpOnly/Secure/SameSite=Lax,
Max-Age 86400, Path = exact view URL) + Location back; cookie GET ⇒ **served bytes == written bytes
(99 == 99)**; CSP on the served page = `sandbox allow-scripts` (live confirmation of §6.1).
Overwrite ⇒ **same URL, same passcode, generation NOT bumped** (raw SQL 1→1), new content served.
Refresh ⇒ generation **1→2 (delta exactly 1)**, passcode rotated, **old grant revoked** (old cookie
bounces to interstitial). `refresh=True` alone on an existing path is refused (ordered checks) —
consistent with spec §5.

**Focus 2 — write-path safety:** traversal set 7/8 rejected with typed `Error: malformed path …` and
zero side effects (`../x.html`, `a/../../b.html`, `/abs/x.html`, `..\x.html`, `\x07`, NUL,
`sub/../../../etc/x.html`) — **the 8th (percent-encoded) is DEFECT D1 below**. Symlink file AND
symlink-dir escapes refused; outside targets untouched; containment resolves BOTH sides
(`view_write.py:367-375`). Cap read from source `MAX_SERVED_BYTES = 33554432`
(`daemon/services/live_views.py:174`): cap+1 ⇒ error **naming the limit** with NOTHING written
(0.26 s); exactly-at-cap ⇒ success, on-disk 33,554,432 B (0.30 s incl. fsync). Atomic write proven:
same-dir mkstemp → fsync → `os.replace` → finally-unlink (`view_write.py:398-429`); `os.replace`
injected to raise (`:413`) ⇒ typed Error, original byte-identical, **zero temp files left**.

**Focus 3 — dual gate (all PASS, mutation-grade):** `designer`/`worker`/`leader`/`tester`/`""` ⇒
typed `Error: view_write is restricted to the 'ari' agent …`; scoped rows + listing unchanged;
`ari` positive control writes + mints. **Literal tool-name allow bypass reproduced at the REAL
resolution path**: non-ari agent with `allow=["view_write"]` ⇒ toolset = exactly 1 tool (RESOLVES —
the documented bypass, `instance.py` category-expansion loop); contrast empty-allow ⇒ 193 tools with
`view_write` stripped (privileged default-deny). Called through the bypass ⇒ typed Error, rows
3→3, files 5→5 (zero side effects, real services behind the double). **Vacuous-counter guard**:
gate neutralized in memory ⇒ the same call succeeds (rows 3→4, file written) ⇒ the zero-side-effect
assertions are proven able to fail. Gate-set separation: `VIEW_WRITE_AGENTS == {"ari"}`
(`views_auth.py:61`) ≠ `BOARD_LINK_AGENTS == {"ari","leader"}` (`:71`); BOTH tools bind the SHARED
`_is_views_authorized` (identity-pinned: `view_write.py:58/:300`, `board_link.py:57/:195`).

#### DEFECT D1 — `view_write` accepts a literal percent-encoded path (severity LOW 🟠)

- **Repro:** `call(view_write, path="%2e%2e%2fx.html", html="<h1>x</h1>", title="T")` as `ari`.
- **Observed:** success envelope; file written at literal `<reports>/%2e%2e%2fx.html` (**inside** the
  root — containment HOLDS, no escape); passcode row minted
  (`('reports','%2e%2e%2fx.html',1,0,False)`); GET of the returned public URL ⇒ **404**
  `{"error":"view not found"}` (the URL framework decodes `%2e%2e%2f` → `../x.html` → serve-side
  validator rejects). Net effect: an unservable orphan page + an unusable minted row + a success
  envelope whose public URL is a lie.
- **Root cause:** `is_well_formed_rel_path` (`daemon/services/live_views.py:259-305`) validates the
  POST-DECODE form by design; its own comment (`:298-303`) says non-decoding callers "MUST apply
  this check to the encoded form". `view_write` is a non-URL entry point applying the validator
  un-decoded. The branch's T-A3 list (`test_view_write_tool.py:342-349+`) has **no** percent-encoded
  case.
- **Adjudication:** the binding plan's path rules (§5) do not name percent-encoding — the letter of
  the plan is met; the leader's verification requirement ("encoded traversal … all rejected") is
  NOT met. No traversal/escape/data exposure occurs (containment proven); the harm is
  litter + write-amplification + a misleading envelope. Recommended fix (pre-merge, small): reject
  `%` in the rel path at validation step 3, or decode-then-revalidate, plus a T-A3 pin case.
- **Side note O2 (environment, not phase-3):** the worktree `.env` dev-fence PG password is stale
  (rejected by local PG16); L1 used unix-socket peer auth (strictly more restrictive). Reconcile
  separately.

### L2 — flagship board page + CSP + XSS + caps + 404 + mount order + reserved names — ✅ 20/21 PASS, 0 code defects

Harness (`/tmp/rt-p3-l2-WFQ5/`, kept for reproduction): in-process FastAPI + REAL
`daemon.routers.live_views.build_router()` + REAL `LiveViewsService` / `ViewPasscodeService` /
`BoardService` / `ViewPasscodeCipher` / `board_link` tool over **real PG `ensemble_dev`** (local PG16,
port 5432, unix-socket peer auth; `SELECT current_database()` asserted per run) + real filesystem
under `mktemp -d`. `data_dir` = `/tmp/rt-p3-l2-WFQ5/data` (never HOME-derived; `rm -rf` prefix-guarded).

> Harness disclosure: `import daemon.api` is impossible in-proc (module-level `app = create_app()` reads
> ambient prod env), so `inject_reports_live_view_root` was **AST-extracted byte-true** from
> `daemon/api.py` and exec'd — the production function, not a re-implementation. The production
> **lifespan was not booted** (deliberate); its call site was read
> (`daemon/api.py:1769` → `inject_reports_live_view_root(config, getattr(app.state, "data_dir", None))`).

| focus | verdict | evidence |
|---|---|---|
| seed real data (statuses/tags/due dates/notes) | ✅ | real `BoardService.create_item`; 4 statuses, 5 tags, 3 due dates + 1 NULL, notes with explicit ascending `ts` |
| mint via the REAL tool + grant via the REAL POST | ✅ | `board_link()` → `/views/public/board/index.html`, 6-char passcode, 5-line shape; POST ⇒ **303**; GET ⇒ 200 `text/html` |
| real-data render, notes oldest-first | ✅ | 27/27 content assertions true; note positions `one=5317 < two=5447 < three=5577`; row order = seam order (`updated_at DESC`) |
| **freshness** | ✅ | raw-SQL title change (rowcount 1) ⇒ next GET carries the new title (8,129 B → 8,153 B, bodies differ) |
| **XSS** | ✅ | raw payloads **absent** (`<script>alert(1)</script>`, `<img src=x onerror=alert(2)>`, `"><img src=x onerror=alert(3)>`); **0** `<script` substrings; parser-level audit (`html.parser`): 0 script tags, 0 event-handler attributes, 0 external URLs; escaped forms present (`&lt;script&gt;…`, `&lt;img src=x onerror=alert(2)&gt;`, `&quot;&gt;…`) |
| no external subresource / no inline script | ✅ | `https://`=0, `http://`=0, `<script`=0, `<link`=0, `<iframe`=0, `javascript:`=0; one nonced `<style>` |
| **board strict CSP** | ✅ | `default-src 'none'; script-src 'none'; style-src 'nonce-…'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'` — all six, **no `sandbox`** |
| **nonce parity** | ✅ | GET#1 `c64255b4…` == body `<style nonce>`; GET#2 `8bb3e2d9…` == body nonce; the two differ; no inline script |
| raw lane `reports` CSP | ✅ | `sandbox allow-scripts` + `nosniff` + `private, max-age=60` |
| CSP scope proof | ✅ | a non-reports root ⇒ **no CSP header**; header diff vs the reports page is exactly `{content-length, content-security-policy}`; `_raw_lane_csp_headers('otherroot')={}`, `('reports')={'Content-Security-Policy': 'sandbox allow-scripts'}` (`routers/live_views.py:131/:169`) |
| **byte cap (CJK)** | ✅ | 50 CJK-heavy rows ⇒ **1,111,158 B (1.060 MiB)** ≤ 2 MiB; second run (180 KB bodies) ⇒ 2,046,495 B (1.952 MiB) with the truncation notice + per-field 64 KiB body cap. Per-field caps `board_view_service.py:108-110` (notes 20 / note 4 KiB / body 64 KiB), render ceiling `board_view_pages.py:61` `MAX_RENDERED_BYTES = 2*1024*1024` |
| **55-row board** | ✅ | disclosure **`Showing 50 of 55 items`**; 50 rows rendered = **the 50 NEWEST** (first body row = `R54-item`; positions strictly descending); the 5 oldest excluded appear nowhere in the body and are **not deleted** (still in DB); global unscoped read is truthful too (`Showing 50 of 166 items`) |
| render failure ⇒ 200 | ✅ | `board_service = None` ⇒ **200** (never 500) + notice `The board backend is currently unavailable — the board could not be read.`; a RAISING backend ⇒ same 200 + same notice (`[LiveViews] board render failed: boom` logged) |
| **uniform 404** | ✅ | no minted row ⇒ board GET 404 (26 B `{"error":"view not found"}`); unknown root ⇒ 26 B; unknown path under the valid `reports` root ⇒ 26 B — **body byte-identical AND header dicts identical** (diff `{}`) |
| **mount order** | ✅ | both halves: route indices — board GET **8**, board HEAD **9**, public catch-all GET **10** (`/views/public/{root_name}/{rel_path:path}`); behavioural: with a minted row + grant the board URL returns board HTML (not JSON, not the 404) |
| **T-B7 via the REAL public POST** | ✅ | correct code ⇒ **303**, `Location: /views/public/board/index.html` EXACT, `Set-Cookie … Path=/views/public/board/index.html` (queryless) EXACT; query-bearing POST ⇒ query-bearing Location + **queryless** Path; wrong code ⇒ failure page + strike (`failed_attempts` 0→1, `deny` audit row); **10th wrong ⇒ locked page** (`Too many incorrect passcode attempts…`, `locked=True`), and the correct code still returns the locked page; `board_link(refresh=True)` restored `generation=17, failed=0, locked=False`; source-scan: `_public_post` (279 lines) contains **no** `is_reserved_root_name` / `has_root` / `RESERVED_ROOT_NAMES` |
| shadowing attempts (board must win) | ✅ | (A) a page physically inside `reports` named `index.html` ⇒ its own URL serves the raw text, but `/views/public/board/index.html` **still renders the BOARD** (canary row, shadow text absent); (B) a root literally named `board` ⇒ **config refuses at construction**; and with the validator BYPASSED (post-validation mutation) the board URL **still renders the board**, `/views/board/index.html` ⇒ 404, catch-all `/views/public/board/sub.html` ⇒ 404 |
| reserved names | ✅ | `RESERVED_ROOT_NAMES == {"public","board"}` (`services/live_views.py:149`); real `view_link` with `root_name="board"` ⇒ typed reserved envelope (`['board','public']`) and **no row minted** (1→1); `/views/board/index.html` ⇒ 404 |
| operator refusal (local config only) | ✅ | `LiveViewsConfig(roots={"board": …})` ⇒ `ValidationError` with the **generic** reserved-set message (names the set, not the retired single-occupant wording) |
| `reports` root out-of-the-box | ✅ | (i) no operator roots ⇒ injection True, `roots = ['designer-artifact','planning','reports','tmp-images']`, path == `<data_dir>/reports`, `allowed_extensions == ['html']`, page reachable (**no silent 404**); (ii) operator-declared `reports` ⇒ injection False, path **unchanged** (never overridden); (iii) missing `data_dir` ⇒ injection False, root absent, **no exception** (fail-soft). Bonus (A-30): relative `./data` ⇒ injected path is **absolute** |

**Discrepancy D-S (spec-bar, NOT a code defect) — full-request SQL count is 6, not ≤4.**
Measured over one authenticated board GET (`before_cursor_execute` on the app's real engine, request
window only): 1 `live_view_passcodes` (gate) + 3 board fetch (items + batched tags + batched notes)
+ 2 filter-option set (items + batched tags) = **6**. Adapter-only fetch = **3** ⇒ the branch's own
pins hold (`tests/unit/routers/test_live_views_board.py:1225` ≤4 adapter, `:1275` ≤8 public render).
The N+1 removal is **proven**: exactly **2** `board_items` queries per request, never one per row
(153 → 3). Positive control: the same listener observed 1 statement on a known-DB interstitial GET
and 6 on a board GET ⇒ the counter is not vacuous.
Adjudication: the ≤4 figure in the verification brief reads as a full-request budget; the spec
(phase3-plan §4.5) targets ≤3 for the **board fetch** and mandates a bounded filter-option call, and
the in-repo pins are calibrated to ≤4/≤8. ⇒ **PASS on the spec's intent** (no N+1, pins green, 3
adapter queries), with the 6-statement full-request figure **disclosed** against the brief's literal
bar. No code change indicated; leader adjudicates.

**Residue after cleanup: 0** — `board_items` / `board_notes` / `board_item_tags` / `projects (p3l2-*)` /
`live_view_passcodes` / `live_view_passcode_audit` all 0; the 6 pre-existing passcode rows and 49
pre-existing projects untouched; `projects` count back to its session-start value.

**L2 coverage gaps (disclosed):** no headless browser (XSS proof is server-side byte + parser-level,
not a live DOM); production lifespan not booted (injection proven via the byte-true production
function); rate-limiter 429 page not driven; `view_write` not invoked (tool lane = L1); T-B10
filter-echo round-trip with `&`/`<` not re-tested; the exact 2 MiB boundary byte not bisected (ceiling
proven by reaching it).

### L3 — suites / marker-correct pins / regression / baseline diff / ensure.md Core — ✅ PASS, zero new failures

Fences held on every command (`POSTGRES_DB=ensemble_dev POSTGRES_HOST=127.0.0.1` on DB-touching
runs; live `:9797` pid 2296421 untouched; base tree via `git archive 39cce206a` into `mktemp -d`,
escape-guarded removal).

**P1 — phase-3 new suites (each run twice: default addopts AND `--override-ini="addopts="`):**

| file | collected | passed | failed | skipped | deselected | exit | runtime |
|---|---|---|---|---|---|---|---|
| `tests/unit/routers/test_live_views_board.py` (suite C) | 50 | 50 | 0 | 0 | **0** | 0 | 36–44 s |
| `tests/unit/tools/test_view_write_tool.py` (suite A) | 44 | 44 | 0 | 0 | **0** | 0 | 21–26 s |
| `tests/unit/tools/test_board_link_tool.py` (suite B) | 23 | 23 | 0 | 0 | **0** | 0 | 10–16 s |

Node-id SET diffs (default vs override): **∅ / ∅ both directions for all three files.**

**P2 — the SEVEN exact-set privileged-category pins (marker-correct, combined):**
`--override-ini="addopts=" -m "integration or not integration"` → **137 passed / 0 failed / 0
deselected / exit 0** (45 s). Per-file: upgrade 21 · attestation 24 · secrets 26 · board 20 ·
self_alarm 24 · maintenancer 13 · service_tool_flag_off 9. The two `len == 12` count pins
(`test_board_registration.py:185`, `test_self_alarm_registration.py:187`) **PASS at runtime**.
**Trap reproduced both ways:** the default-addopts leg of the same 7 files → `128 passed,
9 deselected`, exit 0 — the entire integration-marked file silently dropped while the summary stays
green; the `deselected` token appears only when non-zero. Marker facts:
`test_service_tool_flag_off_byte_identical.py` = 9 `@pytest.mark.integration` decorators;
`test_maintenancer_spawn_resolves_tools.py` = none (default selection).

**P3 — regression:** live-views core 7 files → **326/326** (0 deselected; no file in the set carries
an integration marker); phase-1 board corpus (6 files) → **157/157**; phase-2 self-alarm corpus →
144/146 (**2 pre-existing, base-proven — see F1**); extended live-views adjacency → **216/216**.
**Developer's `50/44/23/316/9/13 = 455` REPRODUCED EXACTLY** — mapping: 50 = suite C, 44 = suite A,
23 = suite B, 316 = the 6-file live-views regression (public_lane 61 + router 33 + live_views_tool
22 + live_views_service 101 + view_passcode_service 74 + migration parity 25), 9 = the
integration-marked pin file, 13 = the unmarked pin file.

**Baseline diff (ours vs the repo's 129F/21E pre-existing):**
- Tool-surface census (`-n 8 --dist loadfile`): HEAD `13 failed, 3915 passed, 7 skipped` vs BASE
  `13 failed, 3848 passed, 7 skipped` — failing-set diff via `comm`: **NEW = ∅ · RESOLVED = ∅ ·
  STABLE = 13**; Δ = **+67 passing nodes = 44 (view_write) + 23 (board_link) exactly**.
- Base-leg method (repo untouched): `git archive 39cce206a | tar -x -C $(mktemp -d)`; the worktree
  venv's `_editable_impl_ensemble.pth` would have imported HEAD code, so the leg **asserted
  `daemon` resolved into the base tree** before trusting any base result.
- Every failure observed in P1–P3 + supplement classified: **(b) file outside the change set +
  pre-existing at base** (self_alarm_tools ×2 — the F1 time-bomb; seal tripwire ×1 — F2; census-13:
  knowledge_tools ×2, prompt_section_reference_integrity ×5, inner_soul_compaction ×3,
  snapshot_tools ×1, instance_tools ×2 — the latter ×2 also QUARANTINE-registered).
- **Our failures in the scoped surface: 0.** The repo-wide 129F+21E was NOT re-derived (whole-tree
  sweep exceeds the 5-min cap and ensure.md scopes to blast radius); the scoped census + base legs
  are the evidence.

**Seal / stale-pin tripwire (runtime, supplement):** the node is **RED** — a hard `pytest.fail`,
not a tolerated set — listing **exactly the 7 predicted stale pins**; base leg @ `39cce206a` =
**the same 7 files with byte-identical baseline/current hashes** ⇒ **phase 3 introduced NO new
stale sealed pin — NEW = ∅, RESOLVED = ∅**. The 7 are pre-existing (none of the 7 is in the
phase-3 change set). Sibling audit file: `tests/test_chart_image_delivery_audit.py` → **46/46
green**.

**L3 findings (non-product, pre-existing):**
- **F1 (LOW, test-debt):** `tests/unit/tools/test_self_alarm_tools.py` hard-codes `daily 23:50`
  (Asia/Ho_Chi_Minh) against the 30-minute floor on the absolute first-fire instant — between
  ~23:20 and 23:50 local the wrapper **correctly refuses** and 2 nodes go red. Faked-noon probe
  ACCEPTS ⇒ time-dependence proven; base leg reproduces identically. Phase-2's "PASS 146/146" was
  recorded at a favorable hour — disclosed. Fix = test-debt commission (relative request or
  far-off time-of-day); not patched here.
- **F2 (LOW, pre-existing):** the seal tripwire's RED state itself (7 stale pins, base-identical) —
  a standing condition awaiting the conventions re-stamp, not a phase-3 item.

---

## 4. Findings for the leader

**One product defect (LOW) and two spec-bar disclosures; zero regressions; both gates hold.**

1. **D1 (LOW 🟠, product defect — pre-merge fix recommended).** `view_write` accepts a literal
   percent-encoded path (`%2e%2e%2fx.html`): the file lands **inside** the root (containment HOLDS —
   no escape), but the page is unservable (public URL 404s — the URL framework decodes the path and
   the serve-side validator rejects it), the passcode row is orphaned, and the success envelope's
   public URL is a lie. Root cause: `is_well_formed_rel_path` validates the post-DECODE form by
   design (its comment says non-decoding callers MUST apply the check to the encoded form);
   `view_write` applies it un-decoded; T-A3 has no encoded case. The binding plan's path rules
   (§5) do not name percent-encoding — the plan's letter is met; the verification brief's
   "encoded traversal … all rejected" is not. Fix: reject `%` in the rel path at validation step 3
   (or decode-then-revalidate) + a T-A3 pin case. ~10 lines, one module + one test file.
2. **D-S (disclosure, not a defect).** The full authenticated board GET issues **6** SQL statements
   against the brief's ≤4 bar (1 passcode gate + 3 board fetch + 2 plan-mandated filter-option
   calls). The N+1 removal is proven (2 `board_items` queries, never per row; adapter-only = 3) and
   the branch's own pins hold (≤4 adapter / ≤8 public render, `test_live_views_board.py:1225/:1275`).
   PASS on intent; the 6 figure is disclosed for the leader's adjudication.
3. **Pin ceremony VERIFIED (both halves):** ONE commit (`f585adea8`) carries both categories +
   all SEVEN pin files; trajectory AST-proven 8→9→10→12; `len==12` pins pass at runtime
   (137/137 marker-correct, 0 deselected). **Manifest correction:** the exact-set pin set is SEVEN
   files (P1/P2 added two) — the "five pins" wording in older `.agents/tester/` prose is stale; the
   tree is self-consistent.
4. **The addopts trap is real and load-bearing (reconfirmed):** the default-addopts leg silently
   drops the 9-node integration-marked pin file while exiting 0 green. Any "all pins green" claim
   must name its invocation. Reproduced both directions.
5. **F1 (pre-existing test-debt, LOW):** `test_self_alarm_tools.py` hard-codes `daily 23:50`
   (Asia/Ho_Chi_Minh); between ~23:20–23:50 local the 30-min floor correctly refuses and 2 nodes
   go red. Phase-2's "PASS 146/146" was recorded at a favorable hour — disclosed honestly. Base
   leg reproduces identically ⇒ not phase-3. Fix = test-debt commission (relative request or
   far-off time-of-day).
6. **F2 (pre-existing):** the seal tripwire is a hard-RED node with exactly the 7 long-standing
   stale pins (base-identical hashes) — **phase 3 introduced NO new stale pin (NEW=∅)** and touched
   zero sealed artifacts; the standing 7 await the commission's step-4 conventions re-stamp.
7. **O2 (environment):** the worktree `.env` dev-fence PG password is stale (rejected by local
   PG16); lanes used unix-socket peer auth (strictly more restrictive). Reconcile separately.
8. **Frozen seam holds:** `git diff -G 'def list_items|def get_item' 99a28e4f3..a2c33c20 --
   daemon/` = EMPTY; phase-1 files untouched; board corpus 157/157. The LITERAL unscoped `-G`
   command is non-empty only because phase-2 docs quote it (tooling artifact, not a violation).

## 5. Baseline diff vs the pre-existing 129F/21E

| surface | ours | base (39cce206a) | verdict |
|---|---|---|---|
| tool census (`tests/unit/tools/`, `-n 8 --dist loadfile`) | 13F / 3915P / 7S | 13F / 3848P / 7S | failing SETS identical (NEW=∅, RESOLVED=∅, STABLE=13); Δ+67 = 44+23 exactly |
| phase-3 suites A/B/C | 117/117 | n/a (new files) | green |
| 7 exact-set pins (marker-correct) | 137/137, 0 deselected | n/a (range edit) | green |
| live-views regression (7 files) | 326/326 | unchanged-green family | green |
| phase-1 board corpus | 157/157 | green | green |
| phase-2 self-alarm corpus | 144/146 | same 2 failures (F1) | pre-existing |
| seal tripwire + audit sibling | 1F (7 stale, F2) + 46/46 | identical 7, byte-identical hashes | NEW=∅ |
| **scoped surface NEW failures** | | | **0** |

The repo-wide 129F+21E was **not re-derived** (whole-tree sweep exceeds the 5-min pack cap and
ensure.md scopes validation to blast radius); the scoped census + per-failure base legs are the
evidence. Quarantine cross-referenced: instance_tools ×2 and the other census families are
registered/known pre-existing rows.

## 6. ensure.md Core

| Requirement | Severity | Status | Evidence |
|---|---|---|---|
| No regressions in changed packs | Critical | ✅ PASS | all scoped packs green (above); census NEW=∅ |
| Deadlock / concurrency integrity (`concurrency_atomic_unit_test`) | Critical | ✅ PASS — baseline-exact ×2 | `99 passed, 74 skipped` / exit 0 at 77.71 s AND 77.87 s — exactly the phase-2 baseline |
| No sync DB calls on the asyncio event loop | Critical | ✅ PASS | same pack, thread-identity nodes green; phase-3's only boot-path edit (`inject_reports_live_view_root`) performs no DB access |
| `dev.sh` includes `--timeout-graceful-shutdown 10` | Critical | ✅ PASS | flag present at `dev.sh:155` (cite the flag, not the line — line numbers drift) |
| Async callers properly await | Important | ✅ PASS | all 10 production call sites awaited (`_get_system_prompt_tokens` ×3, `_compute_context_usage` ×1, `get_queue_stats` ×6); non-awaited sweep ∅ |
| Original deadlock scenario does not block | Important | ✅ PASS (caveat) | the 3 cascade-completion nodes remain module-level SKIPPED (pre-existing structural skip, files byte-untouched by the range) — a pack coverage gap, not a change-set defect; "PASS" ≠ full race coverage |
| No dead code from the fix | Nice-to-have | ✅ PASS | both tools reachable end-to-end (registry → category → factory → produced tool; T-A9/T-BL4 green); shared helper identity-pinned |

**Release Gate: NOT in scope** (additive change set; no migration; the `lifespan` edit is fail-soft,
no DB I/O, covered by 6 unit pins — a live boot smoke on 8079/`ensemble_dev` remains the honest gap,
deliberately not run: `dev.sh`'s exit-trap `pkill` history + Release-Gate scope).
**ensure.md Improvement Notices: NONE** — no requirement's method contradicted the pack/timeout rules.

---

## 7. Overall status

- **Phase-3 verification: ✅ PASS** — all 9 focus areas verified against real code on real PG; gates
  (pin ceremony, frozen seam, migration delta, seal NEW=∅) all hold; zero scoped regressions.
- **One product defect D1 (LOW 🟠)** with repro + recommended ~10-line pre-merge fix — leader
  adjudicates whether it rides the step-4 conventions commit or a dedicated fix commit.
- **Disclosures:** D-S query count (6 vs the brief's ≤4 bar; branch pins hold), F1 wall-clock
  test-debt, F2 standing seal RED (pre-existing), O2 stale dev-fence password (environment).
- **This lane changed no source, no tests, no config; committed only `.agents/tester/` documentation
  (this file + PACKS.md + LESSONS/).**
