# G1 CLOSURE GATE — views-session-bypass (2026-11-09)

**Branch:** `feature/views-session-bypass` @ `87c350578` (base `f49006b92`, 4 lane commits)
**Worktree:** `/home/nea/ensemble-src-wt-views-session-bypass`
**Run:** 2026-11-08 22:26–22:39Z

## G1 VERDICT: **PASS**

The original browser symptom is DEAD end-to-end. Two full authoritative runs
(independent boot + independent minted view path), both with **zero header
injection**.

- run 1 — view `g1root1/gate.md`, boot 22:26Z, browser run 22:33Z
  (evidence kept as `g1-browser-evidence-run1.json`)
- run 2 — view `g1root2/gate.md`, boot 22:37Z, browser run 22:38Z
  (**authoritative** — `g1-browser-evidence.json`; re-run AFTER a concurrent
  foreign edit landed in the worktree, see §Concurrent-edit disclosure)

Both runs produced identical verdicts and identical wire shapes.

## Subject under test

Anonymous browser user, no cookies, navigating over TLS whose certificate is
**untrusted (self-signed)** — the exact precondition that makes Chrome serialise
`Origin: null` on a top-level form POST. Correct passcode typed into the real
form and submitted by clicking the real submit button (no fetch/XHR shortcut).
**No `setExtraHTTPHeaders`, no route interception, no CDP header overrides.**

## Wire chain — authoritative run 2 (CDP, read-only; Chromium 148 headless)

TLS edge `127.0.0.1:8443` (self-signed) → daemon `127.0.0.1:8081`
View: `/views/public/g1root2/gate.md`

### hop 0 — GET interstitial

| field | value |
|---|---|
| request headers | `Origin: <absent>`, `Referer: <absent>`, `Sec-Fetch-Site: none`, `Sec-Fetch-Mode: navigate`, `Sec-Fetch-Dest: document`, `Sec-Fetch-User: ?1`, `Cookie: <absent>` |
| status | **200** |
| `content-type` | `text/html; charset=utf-8` |
| `cache-control` | `no-store` |
| `x-content-type-options` | `nosniff` |
| `referrer-policy` | `no-referrer` |
| `content-security-policy` | `default-src 'none'; script-src 'none'; style-src 'nonce-…'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'` |
| `set-cookie` | `<absent>` |
| DOM | `<title>Enter passcode</title>`; `h1` = “Enter passcode”; `form method=post action=/views/public/g1root2/gate.md`; `input#code` present; `document.scripts.length == 0` |

### hop 0 (POST) — the exact old failure point

| field | value |
|---|---|
| request headers | **`Origin: null`** ← the natural symptom; `Referer: <absent>`; **`Sec-Fetch-Site: same-origin`**; `Sec-Fetch-Mode: navigate`; `Sec-Fetch-Dest: document`; `Sec-Fetch-User: ?1`; `Cookie: <absent>` |
| body | `code=rfxs26` (`application/x-www-form-urlencoded`) — the CORRECT code, single attempt |
| status | **303 See Other** ← was `403 {"error":"forbidden"}` pre-branch |
| `location` | `/views/public/g1root2/gate.md` |
| `set-cookie` | `view_grant=gAAAAABq8Pnw…; Path=/views/public/g1root2/gate.md; HttpOnly; Secure; SameSite=Lax; Max-Age=86400` |
| `cache-control` | `private, max-age=60` |
| `x-content-type-options` | `nosniff` |

All five cookie attributes verified at HEADER level: `Path` scoped to the exact
view lane · `HttpOnly` · `Secure` · `SameSite=Lax` · `Max-Age=86400`.

### hop 1 — redirect followed (PRG)

| field | value |
|---|---|
| request headers | `Origin: <absent>`, `Referer: <absent>`, `Sec-Fetch-Site: same-origin`, `Sec-Fetch-Mode: navigate`, **`Cookie: <present>`** (the `view_grant` just issued) |
| status | **200** |
| `content-type` | **`text/html; charset=utf-8`** |
| DOM | `<title>gate.md - live view</title>`; `h1` = “G1 Gate View”; body contains `G1-CONTENT-MARKER` |

## No-JSON-on-human-surface assertion

| check | run 1 | run 2 |
|---|---|---|
| every human-surface `content-type` | `text/html; charset=utf-8` | `text/html; charset=utf-8` |
| body parses as a JSON error envelope | **false** | **false** |
| body is exactly `{"error":"forbidden"}` | **false** | **false** |
| interstitial reappeared after submit | **false** | **false** |
| view content marker present | **true** | **true** |

## Differential proof that the BRANCH is what changed it

The **base** function (`f49006b92:daemon/routers/live_views.py::_same_host_origin_check`),
executed verbatim from the git object against a stub request:

```
REJECT(403)  <- natural Origin: null (untrusted TLS top-level form POST)
ALLOW        <- Origin: https://127.0.0.1:8443 (trusted cert)
ALLOW        <- Origin absent
```

On the branch the same input yields **ALLOW** (null accepted unless
`Sec-Fetch-Site: cross-site`). Source copy: `logs/base-f49006b92-origin-check.py`.

## Security controls still live (curl probes, same daemon, run 2)

| probe | Origin / Sec-Fetch-Site / Accept | result |
|---|---|---|
| A | `null` / `same-origin` / html | **303** + `Set-Cookie: view_grant=…` (full attribute set) |
| B | `null` / **`cross-site`** / html | **403**, `content-type: text/html` (HTML “Request blocked” page — NOT JSON) |
| C | `https://evil.example` / `cross-site` / html | **403**, `content-type: text/html` |
| D | `null` / `cross-site` / `application/json` | **403**, `content-type: application/json`, body `{"error":"forbidden"}` |
| E | `null` / **absent** (older browser) / html | **303** — the absent-Sec-Fetch fallback keeps the live fix |

So: natural null-origin navigation **allowed**; the cross-site
sandboxed-iframe class **still rejected**; the JSON envelope now exists **only**
for explicit JSON clients — never on a browser surface.

## Optional rider — session bypass (cheap, non-blocking)

Minted one `auth_sessions` row (`subject=g1-rider-operator`) in `ensemble_dev`,
then a public GET carrying **only** `ensemble_sid` (no `view_grant`):

```
HTTP/1.1 200 OK
content-type: text/html; charset=utf-8
"Enter passcode" occurrences: 0
"G1-CONTENT-MARKER" occurrences: 1
```

⇒ a valid session serves the view **directly** — no interstitial, **no grant
minted**. Rider PASS in both runs; the session row was deleted at teardown.
(`logs/*curl-e-session-bypass.{headers,body}`)

## Concurrent-edit disclosure (NOT mine — observed mid-run)

At **22:33:48Z / 22:34:08Z** — while run 1's browser lane was in flight — a
**foreign actor edited** two worktree files:

```
M daemon/routers/live_views.py
M daemon/services/view_passcode_pages.py
```

I did **not** author these edits and did not commit or revert them. I verified
their content is **docs / typing / formatting only** before trusting run 2, by
normalized-AST comparison against `HEAD` (annotations, TYPE_CHECKING blocks and
docstrings stripped):

```
daemon/routers/live_views.py            : AST identical to HEAD = True
daemon/services/view_passcode_pages.py  : AST identical to HEAD = True
git diff | grep origin-gate symbols     : NO hits (byte-identical gate logic)
```

The diffs are: a module-docstring size rationale, `TYPE_CHECKING` imports,
return/param annotations on `resolve_views_session` / `_grant_success_response`,
three comment reflows, the `view_passcode_pages` docstring “five”→“seven”
pages, and a trailing-newline fix. **No behavioural change** — which is why run
2 reproduces run 1 exactly. Both files still `py_compile` clean.

⚠️ Whoever owns those edits: they are **uncommitted in the worktree**. The G1
verdict is valid for `87c350578` + these non-functional edits.

## Artifacts

```
.agents/tester/RESULTS/assets/2026-11-09-views-session-bypass/g1/
├── RUN-LOG.md                            # this file
├── board_before.png                      # interstitial, 1280x800 (run 2)
├── board_after.png                       # rendered view, 1280x800 (run 2)
├── g1-browser-evidence.json              # AUTHORITATIVE machine record (run 2, g1root2)
├── g1-browser-evidence-run1.json         # run 1 machine record (g1root1)
├── logs/
│   ├── daemon.log                        # fenced daemon boot + run (run 1 session)
│   ├── edge.log                          # TLS edge per-request header trace
│   ├── base-f49006b92-origin-check.py    # differential proof source (base fn)
│   ├── curl-{a,b,c,d,e}-*.{headers,body} # run 1 control probes
│   ├── run2-curl-{a,b,c,d,e}.*           # run 2 control probes
│   └── run1-playwright-api-headers.json  # superseded instrument (kept for audit)
└── scripts/                              # reproducible harness
    ├── g1-lib.sh                         # fence + boot + mint + stop
    ├── edge_proxy.py                     # TLS edge, injects X-Forwarded-Proto: https
    ├── edge_start.sh
    ├── g1_browser_cdp.py                 # AUTHORITATIVE browser harness (CDP)
    ├── g1_browser.py                     # first-pass instrument (superseded)
    ├── rider_session.sh                  # session-bypass rider
    └── collect_and_teardown.sh
```

Screenshots also at `/tmp/board_before.png` and `/tmp/board_after.png`.

## Safety ledger

- Ambient `POSTGRES_*` (live `ensemble_prod` @10.44.0.2) scrubbed **per
  variable**; worktree `.env` sourced; every DB touch asserted
  `POSTGRES_DB=ensemble_dev`. **Zero prod contact.**
- `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` exported explicitly on every daemon
  boot — never `env -u`.
- Ports 8081/8443 only; **8088 never touched**. Every kill port-verified
  (`ss -ltnp` + `pid=` match) before signalling.
- Fence roots `mktemp -d /tmp/g1lv.XXXXXX`, escape-guarded on removal.
- One drill Fernet key per boot; fresh view paths per run (`g1root1`, `g1root2`)
  ⇒ no rotation-required collision with persisted rows.
- Token bucket respected: 1 browser submission + 4 paced curl probes per run;
  passcode row ended `failed_attempts=0, locked=false, generation=1` in both runs.
- No Docker, no bare `python` (`.venv/bin/python`), no live-shaped HOME paths.

## Anomalies / notes (non-blocking)

1. **Instrument iteration.** Playwright's JS-level `request.headers` is a
   *filtered projection* — it surfaced `origin: null` but omitted `sec-fetch-*`.
   Switched to CDP `Network.requestWillBeSentExtraInfo` (wire-level) +
   `Network.responseReceivedExtraInfo` (raw `Set-Cookie`), correlating hops by
   `(requestId, hop index)` because redirect hops reuse the requestId. The
   superseded output is kept at `logs/run1-playwright-api-headers.json`.
2. **Screenshot verification.** `explain_image` refused the PNGs (path outside
   its workdir boundary), so content verification rests on DOM extraction
   (`document.title`, `h1`, marker text, `document.scripts.length`) plus a raw
   PNG decode confirming both files are 1280×800 and non-blank. The wire record
   is the authoritative evidence, not the pixel diff.
3. **Boot exit-78 on the first attempt.** The ambient daemon-child env carried
   the LIVE `ENSEMBLE_AUTH_DISCOVERY_URL` (origin `https://door.ensem.dev`),
   which mismatched the drill issuer. Fix = unset `ENSEMBLE_AUTH_DISCOVERY_URL`
   / `ENSEMBLE_AUTH_EXTERNAL_REDIRECT_BASE` per variable before boot. Worth
   carrying into future fenced auth-ON boots on this host.
4. **Benign 404** in the browser console is `/favicon.ico` (no favicon route).
5. **Did not run** the repo unit pack (`tests/unit/routers/test_live_views_session_bypass.py`)
   — out of scope for G1 (browser closure gate); lane review already recorded
   74/74 passing.
