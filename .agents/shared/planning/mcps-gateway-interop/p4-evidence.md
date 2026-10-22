# Phase-D1 / P4 — evidence log (the atomic commit: D3c → D3b → D2 → D3a(FE) + the 14 pins + the roster fix)

> **VERDICT: PASS — ONE atomic commit, all five named verification
> suites green (98 + 53 FE + 34 + 3 + 1), every one of the §2.D2.1
> pins 1–14 dispositioned, the roster-release fix proven by the #16c
> interop gate.**
>
> Branch `feature/mcps-gateway-interop`, base HEAD `1438d2e65` (P3
> evidence). The commit body is ordered EXACTLY D3c → D3b → D2 →
> D3a(FE)+pins → roster fix (B10: not parallel, not splittable). The
> roster defect mechanics are cited in the commit message verbatim
> from the P2/P3 rulings.

---

## 1. The degraded contract as implemented (D2)

**Envelope.** `GatewayReadEnvelope(BaseModel)` in
`daemon/routers/mcps_gateway_schemas.py` — `degraded: bool = False`,
`error: str | None = None`; `GatewayMachinesResponse`,
`GatewaySessionsResponse` and `GatewayAuditResponse` inherit it
(`count` / `gateway_enabled` stay where they were, in the children).

* **200, never 503** (AD4): 503 stays reserved for "surface not
  wired"; the FE keeps mapping 503 → `gateway_unavailable`.
* `error` carries ONLY the short non-secret classification
  `"audit_unavailable"` — never a raw exception string (pinned: the
  raising-reader test embeds a fake secret DSN in the raised error and
  asserts it never reaches the body).
* **Permanently-false lanes, documented** in the envelope docstring:
  machines/sessions are in-memory lanes and answer `degraded: false`
  forever — truthful symmetry (only `/audit` can set it today).
  Asserted live: the same-outage orthogonality unit test, the three
  updated unit key-set pins, and the three #23 interop wrapper gates
  (`degraded=False` observed on the real daemon).
* **Knob.** `MCPS_GATEWAY_READ_TIMEOUT_MS` (default `5000`) in
  `daemon/services/mcps_gateway/config.py` (`DEFAULT_READ_TIMEOUT_MS`,
  `GatewayConfig.read_timeout_ms`, `_env_int` + positive check,
  family-consistent `READ_TIMEOUT_MS` env suffix, knob-table docstring
  row). The docs.md knob-table ROW stays P5/D5 property per the
  boundary (no docs riders beyond the #14 needle); the docs knob pin
  is one-directional (every pinned knob must appear) so no doc pin
  broke.

**Both swallow layers fixed (AD5).**

* `daemon/repositories/mcps_gateway/repository.py` — the read-side
  swallow DELETED; `list_recent` RAISES. Pinned by
  `test_audit_repository_raises_when_engine_dead` (unreachable-DSN
  engine `postgresql+psycopg://127.0.0.1:1/ensemble_dev_test_only`).
* `daemon/services/mcps_gateway/service.py` — `audit_tail` returns the
  classified triple `(rows, degraded, reason)`: `False` for healthy
  AND for a write-only sink (`NullAuditSink` ⇒ empty tail, NOT
  degraded — pinned); `True + "audit_unavailable"` when the reader
  raised.
* The WRITE-path swallow at `service.py:~1139` STAYS untouched (an
  audit-write failure must never abort a command — a different,
  correct rule; re-stated in `audit_tail`'s docstring).

**The bounded read.** `daemon/routers/mcps_gateway_read.py:236` was
the only unbounded read; it is now
`await asyncio.wait_for(asyncio.to_thread(service.audit_tail, …),
timeout=service.config.read_timeout_ms / 1000)` with
`asyncio.TimeoutError` ⇒ the SAME degraded body.

**The outage shape is asserted as BODIES, not logs** (plan §4):

| Construction | Test | Body asserted |
|---|---|---|
| 1+3 dead/unreachable engine (port 1, deterministic refuse — replaces the FALSE `dispose()` premise) | `test_audit_read_degraded_on_unreachable_engine` | 200 == `{audit: [], count: 0, limit: 50, offset: 0, gateway_enabled: True, degraded: True, error: "audit_unavailable"}` |
| 2 monkeypatched raising reader | `test_audit_read_degraded_on_raising_reader` | same body; classification lives in `audit_tail`; the secret DSN in the raised error never leaks |
| 4 hung reader (sleeps 10× the 200 ms timeout) | `test_audit_read_degraded_on_hung_reader` | 200 + `degraded: true` + `error` set + **elapsed < 1.5 s** |
| negative: write-only sink | `test_a_write_only_sink_is_not_degraded` | `([], False, None)` |
| orthogonality | `test_machines_and_sessions_stay_healthy_on_the_same_outage` | machines/sessions `degraded: false`, `gateway_enabled` unchanged |

**Harness finding (NEW, disclosed):** the hung-reader test cannot use
starlette's `TestClient` — its anyio blocking portal JOINS the loop's
default executor on request teardown, so the client wall clock
measures the reader-thread JOIN (2.01 s), not the handler bound (a
plain-loop probe measured 0.20 s). The test therefore uses
`httpx.AsyncClient` + `ASGITransport` (the repo's established pattern,
`tests/test_daemon_settings_api.py:222`), which returns when the
HANDLER returns — the production behaviour (uvicorn never joins the
executor per request). Documented in the test's docstring.

**FE (D2 half).** `degraded?: boolean` + `error?: string | null`
OPTIONAL on the three response interfaces (B3b — the 8 typed literals
compile untouched; ONE new spec pins the default: a body WITHOUT
`degraded` renders healthy). `mcps-gateway.service.ts` surfaces
`degraded` / `degradedSource` (computed, first degraded lane); a
degraded audit read does NOT blank the last page (rows, offset and
pager state survive — pinned), and recovery clears the flag (pinned).
`component.html` renders a DISTINCT degraded banner
(`data-test="degraded-banner"`, `role="status"`, info-blue accent in
the scss — visually distinct from both the red error banner and the
amber disabled banner); the error banner does NOT render alongside
(pinned). Per the P3/live ruling the W8 semantics implemented are:
with a 200 the tap DOES run, so the guard SKIPS the assignment
deliberately — the last page survives by design, and the test asserts
exactly that (rows + banner), not "the tap never runs".

## 2. The three riders as landed

**D3c — the ONE projection helper, at the ROUTER only.**
`AUDIT_WIRE_KEYS` + `audit_row_to_wire(row: AuditRecord | Mapping)`
live next to `AuditRecord` in `daemon/services/mcps_gateway/audit.py`;
the helper is shape-agnostic (`created_at` OR `timestamp`), idempotent
(an already-projected dict in ⇒ the same dict out), never synthesizes
a stamp, `""`→`None` for the stamp + ids, `None`→`""` for text, never
emits `id`/`detail`. It is INSTALLED at exactly one point — the
router's `_audit_entry` (now `GatewayAuditEntry(**audit_row_to_wire(row))`).
**Watch-list #1 honored:** NOT installed inside
`MemoryAuditSink.list_recent` (which would have broken pin #12's
`rows[0]["created_at"]` read); the memory sink's dict shape is
untouched — only its DOCSTRING was corrected ("the same keys except
`id`" — Rev-2 B1 divergence (a)). The write path (`repository.py:51-66`)
is excluded BY DESIGN (persists `id`+`detail`); no "unification".
Proof: `tests/unit/test_mcps_gateway_audit_projection.py` (NEW) —
`test_memory_sink_row_and_repo_row_project_to_the_same_wire_dict`
(byte-identical wire dicts across BOTH sinks, real stamp), the
unset-stamp case via RAW SQL through the engine BYPASSING `record()`'s
`now_utc_iso()` substitution (watch-list #3 — through-`record()` would
silently stamp and prove nothing; `''` is legal against the NOT NULL
TEXT column), idempotence, key set == `AUDIT_WIRE_KEYS` for both input
shapes, `""`↔`None` both directions, and `id`/`detail` never emitted.

**D3b — the ordering rider.**
`ORDER BY created_at DESC, id DESC` in `list_recent`. The docstring
states plainly: `id` is uuid4 TEXT ⇒ **paging stability, NOT
chronological order within a same-stamp tie**; the memory-sink
divergence (no `id`; stable sort ⇒ insertion order) is documented, not
chased. Acceptance (the pin that did not exist): two same-stamp rows
written through the real path —
`test_the_tie_order_is_deterministic_across_reads` and
`test_paging_across_the_tie_returns_two_different_rows`
(`limit=1/offset=0` vs `offset=1` ⇒ two DIFFERENT `id`s).

**D3a — FE 403 honesty (rawCode-first, AD7).**
`mcps-gateway.service.ts:decodeError` branches, in order:
1. `rawCode === 'SCOPE_INSUFFICIENT'` FIRST — the server's message
   preserved verbatim AND its `required` list appended:
   `"Token lacks required scope (needs mcp:read)"` against the live
   nested `{detail:{error,message,required}}` decorator body
   (live-pinned in this same phase's p3 run: gate
   `the-403-body-is-NESTED-under-detail-with-SCOPE_INSUFFICIENT`).
2. `rawCode === 'AUTH_REQUIRED' || status === 401` — the flat
   middleware body (live-pinned: `the-401-body-is-FLAT`).
3. The redirect/HTML guard — a 2xx reaching the error path (JSON parse
   failure) or an HTML-sniffed body renders `not_json`, NEVER
   `http_200`; a success-path guard also routes a delivered-HTML body
   to the same label without assigning rows.
4. `status === 403` without a known code — generic `forbidden` + the
   server's raw message (`CSRF_ORIGIN_MISMATCH` must NOT render
   SCOPE_INSUFFICIENT — pinned).
5. 503 `gateway_unavailable` / `network_error` / `http_<status>`
   unchanged.
`Accept: application/json` (the upstream-decided defensive header,
`JSON_HEADERS`) is sent on all three reads — the middleware's
302-on-wildcard trap is live-pinned in the same run
(`no-token-with-accept-wildcard-is-a-302-to-the-login-page-not-401`).
Model doc-comments (`models/mcps-gateway.model.ts`) and the component
doc-comment restate the label vocabulary. Acceptance, all pinned:
403-CSRF ≠ SCOPE_INSUFFICIENT; 403-SCOPE keeps `required`; 401 renders
AUTH_REQUIRED (a NEW test — the FE had ZERO 401/AUTH_REQUIRED pins);
HTML renders non-`http_200`; the Accept header is asserted on every
request.

## 3. Per-pin disposition (§2.D2.1, 1–14)

| # | Pin | Disposition in THIS commit |
|---|---|---|
| 1 | read.py:156 machines key set | UPDATED — key set + `degraded is False` + `error is None` (VALUE, not presence) |
| 2 | :206 empty-roster exact dict | UPDATED — `+ "degraded": False, "error": None` |
| 3 | :213 sessions key set | UPDATED — as #1 |
| 4 | :248 audit key set | UPDATED — as #1 |
| 5 | :361 disabled-gateway exact dict | UPDATED — `+ degraded/error` (carries disabled ≠ degraded, AD4) |
| 6 | :368 sessions disabled exact dict | UPDATED — as #5 |
| 7 | :476 scope-decorator 403 body | CONSUMED, untouched (D3a's BE contract; `required` read from `detail.required`) |
| 8 | :437 middleware 401 body | CONSUMED, untouched (BE 401 body unchanged; `daemon/middleware/auth.py` NOT touched) |
| 9 | service.spec:334-348 flat 403 | UPDATED — the server's message preserved verbatim (no hard-coded label) |
| 10 | service.spec:350-364 nested 403 | UPDATED — live shape + `required` preserved: "Token lacks required scope (needs mcp:read)" |
| 11 | component.spec:291-303 banner | UPDATED — new truthful message |
| 12 | read.py:499-503 audit_tail caller | UPDATED — unpacks the triple; `degraded is False` asserted; `rows[0]["created_at"]` unchanged |
| 13 | the 8 typed literals | RESOLVED by B3b (`degraded?: boolean` optional) — none changed; ONE new default spec (body without `degraded` ⇒ healthy) |
| 14 | docs.md:856 + docs pin :763-766 | UPDATED TOGETHER — the 403 row now carries the new message; a FOURTH row (degraded audit read) added; the docs-test docstring now says "four"; new needle `"degraded: true"` |

**The stated gap closed:** the FE had ZERO `401|AUTH_REQUIRED` matches
— NEW tests landed for the 401 branch (service decode + component
banner) AND for the degraded banner (component) + the degraded lane
behavior (service), plus the not_json guard and the Accept header.

**Beyond the 14 (unavoidable, in-scope):** `test_real_pair.py`'s three
`*_WRAPPER_KEYS` frozensets (assertion #23) were exact key-set pins on
the SAME envelopes — updated with `degraded`/`error`, the three #23
gates now also assert `degraded is False` BY VALUE, and the gate names
say so. The wrapper-keys comment marks D2 as the sanctioned growth.

**Carry-forwards applied (file touched):** the `error_code` pinning on
the 3 marker rows tightened — the error row now pins
`error_code == echo_error_code` (the typed client code), the timeout
row pins `error_code == "TOOL_TIMEOUT"` by value, and each of the 3
DIVERGENCE_MARKERS gains a NEW DB-side gate
`marker-<name>-the-db-row-carries-the-TYPED-error-code` (correlated by
args_hash, pinned to `TOOL_ERROR`). NOT touched (carry-forward
inapplicable): `go_client.py` skipped-line count, `p0_gate.py:635`
static message.

## 4. Roster-release fix (the one daemon behavior change beyond D2/D3)

`daemon/services/mcps_gateway/service.py:close_connection` — the
§8.3 roster pointer release (`_store.mark_offline`) moved ABOVE the
awaited `await connection.transport.close()`; the session-manager
release (`end_for_connection`) stays FIRST, so the §8.3 lock is never
held twice. Defect mechanics (cited in the commit message): the close
handshake of a WEDGED peer can stretch for seconds, and the read
surface kept reporting the dead session as `busy: true` through that
whole window — a stale busy:true read-surface window, not a double
lock; found by interop gate **#16c failing honestly in P1/P2**
(`the-session-is-gone-and-the-roster-no-longer-points-at-it`,
P3-hardened against a vacuous vanished-row pass).

**Proof:** `tests/integration/mcps_gateway/test_real_pair.py` —
**3 passed** (P1+P2 regression incl. #16c + P3), against the real
shipped Go client, uvicorn-native TLS on an ephemeral port, AUTH ON,
and the real PostgreSQL fence DB `ensemble_dev_test_only`. Observed
live in this phase's p3 capture:
`the-busy-flag-transitions-true-then-false-across-begin-end —
pre=False live=True post=False post_session_id=None` and the #23
wrapper gates `degraded=False`.

## 5. Verification (named suites ONLY — all green)

| # | Command | Result | Wall |
|---|---|---|---|
| 1 | `timeout 300 uv run pytest tests/unit/routers/test_mcps_gateway_read.py tests/unit/repositories/test_mcps_gateway_audit_repository.py tests/unit/test_mcps_gateway_docs.py tests/unit/test_mcps_gateway_audit_projection.py -q` (incl. the NEW projection file) | **98 passed** | 7.5 s (5.06 s pytest) |
| 2 | `cd frontend && npx jest src/app/pages/mcps-gateway/mcps-gateway.service.spec.ts src/app/pages/mcps-gateway/mcps-gateway.component.spec.ts --silent` | **2 suites, 53 passed** | 2.3 s (1.53 s jest) |
| 3 | `timeout 240 uv run pytest tests/integration/mcps_gateway/test_mcps_gateway_protocol.py tests/integration/mcps_gateway/test_fake_client_selftest.py -m integration -q` | **34 passed** | 26.9 s |
| 4 | `timeout 300 uv run pytest tests/integration/mcps_gateway/test_real_pair.py -m integration -q` | **3 passed** | 34.0 s |
| 5 | `timeout 300 uv run pytest tests/integration/mcps_gateway/test_p0_gate.py -m integration -q` (run ONCE) | **1 passed** | 12.0 s |

Regression extras (the plan's D2/D3 gate, run early — all code-final):
`tests/unit/test_mcps_gateway_service.py` + transport + config/tools +
tool_filter → **120 passed**. `tests/integration/test_mcps_gateway_ws.py`
grepped: it pins no read-envelope shape (asserts the sink directly) —
no landmine outside the named suites.

**P0-gate evidence append (DISCLOSED):** suite 5 appended a 91-line
run block to `.agents/shared/planning/mcps-gateway-interop/p0-gate-evidence.md`
(attempt PASS 2026-10-22T17:32:55Z) — committed with this phase per
the mission. Pre-existing cosmetic note (NOT touched, out of scope):
the gate line `[PASS] audit-row-no-raw-arguments — the row carries the
raw arguments` has an inverted DETAIL string (the verdict is correct).

**Teardown proof (interop runs):** p1 direct re-run ended
`verdict: PASS, mismatches: []` with gates
`no-orphan-stub-child pids=[]`, `client-process-stopped exit=0`,
`no-listener-left remaining=[] baseline=[]`,
`gateway-port-closed port=56830 listening=False`; the p0 run's
evidence tail records the same closure (client stop report SIGTERM
exit 0, no listeners). Ephemeral ports only; nothing left listening.

## 6. Scope notes (disclosed, for the record)

* **One file beyond the plan's enumerated FE list:**
  `mcps-gateway.component.scss` (+12 lines) — a DISTINCT banner needs a
  distinct visual treatment; the plan's file map predates the banner.
  No other rider.
* The new unit file landed at `tests/unit/test_mcps_gateway_audit_projection.py`
  (flat, the directory's convention) rather than the plan's
  `tests/unit/services/` path — that directory does not exist; the
  flat `tests/unit/test_mcps_gateway_*.py` family is the established
  shape.
* The hung-reader harness finding (§1) — TestClient's portal joins the
  default executor — is a NEW finding worth a note in any future
  bounded-read test in this repo.
* `docs/mcps-gateway.md` §9.5 is the ONLY doc region touched (the #14
  needle: the 403 row + the fourth row); the knob-table row for
  `MCPS_GATEWAY_READ_TIMEOUT_MS` remains P5/D5 work, and the docs
  knob-default pin is one-directional so nothing broke.

## 7. Commit

ONE atomic commit on `feature/mcps-gateway-interop`, body ordered
D3c → D3b → D2 → D3a(FE)+pins → roster fix, with the roster defect
mechanics cited. SHA recorded in §8 below after the push (no force).

— P4 (Coder), 2026-10-22

## §8 — Commit

Phase-D1 / P4 phase commit: `c2796ed99ae88fb26f785dc1bfd5daffd679e473`
(recorded post-push by the evidence-correction commit — a self-referencing SHA cannot live inside its own commit).
