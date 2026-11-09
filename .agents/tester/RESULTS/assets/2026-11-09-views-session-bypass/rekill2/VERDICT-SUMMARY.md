# RE-KILL 2 + INDEPENDENT THIRD-CLASS SWEEP — VERDICT SUMMARY

**Lane:** views-session-bypass / anomaly 6c543ecc (RE-KILL + third-class hunt)
**Tip under test:** `4be947eeebcc1faf7e81c3c83fd25430c454398f` (verified FIRST, before any probe)
**FIX-10 commit under test:** `523c5eed7` (test pin) / `ac5f024fc` (the fix) — "forwarded anchors constrained to declared set (class closure)"
**Worktree:** `/home/nea/ensemble-src-wt-views-session-bypass` (branch `feature/views-session-bypass`)
**Port:** 8082 ONLY (two sequential boots). 8088 never touched; 8079/8443/9797 never touched.
**Runtime:** 2026-11-09T02:43Z → 03:21Z (~38 min wall; ~25 min of probing, pace-bound by the verify rate limiter)

---

## 1. VERDICT (headline)

| Question | Answer |
|---|---|
| Is the 6c543ecc exploit dead (re-kill)? | **YES — DEAD.** 3/3 spaced re-kills ⇒ 403 `text/html`, **NO Set-Cookie**, `failed_attempts` **UNCHANGED** (0). |
| Did the controls still work on the same boot? | **YES.** Legit proxied shape (`XFH: views.example` + `Origin: https://views.example`) ⇒ **303 + grant cookie**; `Origin: https://evil.example` (no XFH) ⇒ **403**. |
| **THIRD DISTINCT BYPASS CLASS FOUND?** | **NO.** |
| Third-class hunt coverage | 75 wire probes across 10 groups + the session path + the board lane + the origin-parser edges. |
| Grants issued from a non-allowed source | **ZERO.** Every 303 traces to one of: the operator-declared base / the operator audience / the exact-loopback carve / absent-Origin / null-Origin / a valid session. |

**No escalation.** The escalation trigger is *"any grant issued from a source that is neither operator-vouched, nor exact-loopback carve, nor absent/null-Origin, nor valid-session"* — **no such grant was observed on any of the 75 probes.** Loop 3 is therefore NOT required on evidence.

---

## 2. Three brief-vs-implementation divergences (NOT escalations — adjudicate)

These are the only places where the observed result differs from the commission brief's prediction. In every case the **repo's own pinned contract says the observed behaviour is correct**, and the grant source is still the operator-vouched anchor — so none of them meets the escalation definition. They are reported because the brief's predictions were wrong and the council should know which contract it is actually shipping.

### D1 — Trailing-dot on the vouched anchor ⇒ **303** (brief predicted 403)
* Probes **B6.2** (`Host: views.example.` + `Origin: https://views.example.`) and **C4** (`Origin: https://op.example.`) ⇒ **303 + grant**.
* **Pinned as intended:** `test_k6_normalization_equivalence_no_escape` asserts `("views.example.com.", "https://views.example.com.", 303)` — *"trailing-dot (FQDN) form of the declared host ⇒ grant"*. `_views_host_key` (`daemon/routers/live_views.py:1021`) documents the root-dot equivalence ("the FQDN root-dot form `example.com.` ≡ `example.com`") and is applied on **both** sides of every comparison.
* Cross-form probes B6.3 / B6.4 ⇒ 303 (dot and non-dot interoperate).
* **Reading:** `example.com.` *is* the same DNS name as `example.com` (browsers strip the root dot before cert matching), so the equivalence is sound. The brief's "config-exact vouch, trailing dot ≠ same name" is the wrong expectation for this implementation.
* **Grant source:** the operator-vouched anchor (normalised spelling). Not a third class.

### D2 — Port-elision on the vouched anchor ⇒ **303** (brief predicted 403 for `:8443`)
* Probes **B5.1** (`Host: views.example:8443` + `Origin: https://views.example:8443`), **B5.5** (`:0`), **B5.6** (`:65535`), **B5.7** (`https://views.example:` — empty port silently dropped by `urlsplit` on Python 3.14), **C12** / **C13** (audience shape) ⇒ **303 + grant**.
* **Pinned as intended:** `test_portless_elision_preserved_under_declared_anchor` asserts **303** for `Host: example.com` + `Origin: https://example.com:8443` under a declared base — *"the FIX-3 disclosed behavior survives, anchored"*. `_host_matches` (`live_views.py:998`): a portless side is port-compatible with any origin port.
* **Reading:** the *port* of a vouched name is not vouched; a portless declaration vouches the name across ports. The brief's "only :443/default elision may grant" is stricter than the shipped (and pinned) contract.
* **Residual risk (for the council, not a defect):** the port dimension of a vouched name is unconstrained, and the *scheme* is ignored entirely (**B10.5**: `Origin: http://views.example` ⇒ 303 against an `https://` declaration — *"the scheme never participates"* in `_host_matches`). Neither is browser-exploitable for CSRF (a browser's Origin always names the page's own origin, so a grant still requires the operator's own host), and neither lets an off-site page obtain a grant.

### D3 — "evil-first must not poison" ⇒ the first value **DOES** poison (403), even for a matching Origin
* Probe **B1.2** (`XFH: evil.example, views.example` + `Origin: https://views.example`) ⇒ **403**.
* **Pinned as intended:** `test_k4_multi_value_first_value_rules` — the FIRST value rules; a contradicting first value ⇒ 403 for *any* Origin. Mirrors K2 (a mismatching forwarded value **alone** vetoes, even the declared-matching Origin) — probe **B3.4** confirms the RFC 7239 equivalent.
* **Reading:** the brief's parenthetical was ambiguous; the shipped contract is "first value rules, and a contradicting forwarded value vetoes the whole request". Stricter than the brief's alternative reading — no bypass either way.

---

## 3. Probe ledger — expected vs observed

Legend: **MATCH** = observed == expected (the expectation set from the repo's pins / the code); **RECORD** = no assertion (commission said "record"), observation given.
Every probe: browser Accept, `X-Forwarded-Proto: https`, correct passcode (except E-run-1 — see §6), `Host` = the internal proxy hop `internal-svc:8079` unless the probe varies it.

### 3.1 GROUP A — the exploit must die + controls (BOOT 1, declared front)

| # | Probe | Expected | Observed | Verdict |
|---|---|---|---|---|
| A1 | `XFH: evil.example` + `Origin: https://evil.example` | 403, no cookie | **403** `text/html`, `set-cookie: 0` | MATCH |
| A2 | same (spaced 14 s) | 403 | **403**, `set-cookie: 0` | MATCH |
| A3 | same (spaced 14 s) | 403 | **403**, `set-cookie: 0` | MATCH |
| A4 | control `XFH: views.example` + `Origin: https://views.example` | 303 + grant | **303**, `set-cookie: 1` | MATCH |
| A5 | control `Origin: https://evil.example` (no XFH) | 403 | **403**, `set-cookie: 0` | MATCH |

`failed_attempts` before = after = **0** across the whole A cluster.

### 3.2 GROUP 1+2 — XFH multi-value order games + multi-LINE

| # | Probe | Expected | Observed | Verdict |
|---|---|---|---|---|
| B1.1 | `XFH: evil.example, views.example` + Origin evil | 403 | 403 | MATCH |
| B1.2 | same XFH + Origin **views** | 403 (K4 first-value) | 403 | MATCH |
| B1.3 | `XFH: views.example, evil.example` + Origin views | 303 | 303 + cookie | MATCH |
| B1.4 | same XFH + Origin evil | 403 | 403 | MATCH |
| B2.1 | two XFH **lines** `[evil / views]` + Origin evil | 403 | 403 | MATCH |
| B2.2 | two XFH lines `[evil / views]` + Origin views | 403 | 403 | MATCH |
| B2.3 | two XFH lines `[views / evil]` + Origin views | 303 | 303 + cookie | MATCH |
| B2.4 | two XFH lines `[views / evil]` + Origin evil | 403 | 403 | MATCH |

**Which line wins:** the **FIRST line** wins, and the validation follows it. Mechanism confirmed both on the wire (B2.2 403 vs B2.3 303 — the *first* line decides) and locally: Starlette's `Headers.get` returns the **first** occurrence of a duplicated header name (local check: `[evil, views] → 'evil.example'`, `[views, evil] → 'views.example'`). The second line is never read.

### 3.3 GROUP 3+4 — RFC 7239 `Forwarded` variants + Referer fallback

| # | Probe | Expected | Observed | Verdict |
|---|---|---|---|---|
| B3.1 | `Forwarded: host=views.example, host=evil.example` + Origin evil | 403 | 403 | MATCH |
| B3.2 | same + Origin views | 303 | 303 + cookie | MATCH |
| B3.3 | `Forwarded: host=evil.example, host=views.example` + Origin evil | 403 | 403 | MATCH |
| B3.4 | same + Origin views | 403 (K2 veto) | 403 | MATCH |
| B3.5 | `Forwarded: for=1.2.3.4;host=evil.example` + Origin evil | 403 | 403 | MATCH |
| B3.6 | `Forwarded: for=1.2.3.4;host="views.example"` + Origin views | 303 | 303 + cookie | MATCH |
| B3.7 | `Forwarded: host=views.example:443` + Origin views | 303 | 303 + cookie | MATCH |
| B4.1 | no Origin + `Referer: https://evil.example/x` | 403 (K7) | 403 | MATCH |
| B4.2 | no Origin + evil Referer + forged XFH evil | 403 | 403 | MATCH |
| B4.3 | no Origin + `Referer: https://views.example/x` | 303 (K7) | 303 + cookie | MATCH |

**Rule observed:** the FIRST `Forwarded` **element**'s `host=` parameter rules (the second element is ignored); the quoted RFC 7239 form is unquoted and accepted; a mismatching forwarded value vetoes **alone** (B3.4 — even the declared-matching Origin is rejected). The Referer is judged by the same anchor set as the Origin (K7) — it can never vouch for a host, and it inherits the veto.

### 3.4 GROUP 5 — port forms on the vouched anchor (see D2)

| # | Probe | Expected | Observed | Verdict |
|---|---|---|---|---|
| B5.1 | `Host: views.example:8443` + `Origin: https://views.example:8443` | 303 (pinned elision; **brief said 403**) | 303 + cookie | MATCH |
| B5.2 | `Host: views.example:443` + `Origin: https://views.example:443` | 303 | 303 + cookie | MATCH |
| B5.3 | `Host: views.example` + `Origin: https://views.example:443` | 303 | 303 + cookie | MATCH |
| B5.4 | `Host: views.example:443` + `Origin: https://views.example` | 303 | 303 + cookie | MATCH |
| B5.5 | `Origin: https://views.example:0` | 303 (record) | 303 + cookie | MATCH |
| B5.6 | `Origin: https://views.example:65535` | 303 (record) | 303 + cookie | MATCH |
| B5.7 | `Origin: https://views.example:` (trailing colon ⇒ empty port dropped) | 303 (record) | 303 + cookie | MATCH |
| B5.8 | `Origin: https://views.example:notaport` | 403 (malformed port ⇒ unparseable) | 403 | MATCH |
| B5.9 | `Origin: https://views.example:99999` | 403 (out of range) | 403 | MATCH |

### 3.5 GROUP 6+7 — case / trailing dot / IPv6 / IDN (see D1)

| # | Probe | Expected | Observed | Verdict |
|---|---|---|---|---|
| B6.1 | `Host: VIEWS.EXAMPLE` + `Origin: https://views.example` | 303 (case-normalised) | 303 + cookie | MATCH |
| B6.2 | `Host: views.example.` + `Origin: https://views.example.` | 303 (**brief said 403**) | 303 + cookie | MATCH |
| B6.3 | `Host: views.example.` + `Origin: https://views.example` | 303 | 303 + cookie | MATCH |
| B6.4 | `Host: views.example` + `Origin: https://views.example.` | 303 | 303 + cookie | MATCH |
| B7.1 | `Host: [::1]:8082` + `Origin: https://[::1]` | 303 (exact-loopback carve — allowed source) | 303 + cookie | MATCH |
| B7.2 | `Host/Origin: xn--views-4ua.example` (punycode lookalike) | 403 | 403 | MATCH |
| B7.3 | `Host/Origin: vıews.example` (raw UTF-8 confusable) | 403 (record) | 403 `text/html` | RECORD (= expected) |
| B7.4 | `Host/Origin: views.example.evil.com` (suffix lookalike) | 403 | 403 | MATCH |

### 3.6 GROUP 9+10 — board lane (second gate surface) + declared-base / veto parse edges

| # | Probe | Expected | Observed | Verdict |
|---|---|---|---|---|
| B9.1 | **BOARD** re-kill: forged XFH evil + Origin evil | 403 | 403 | MATCH |
| B9.2 | BOARD control: XFH views.example + Origin views.example | 303 | 303 + cookie | MATCH |
| B9.3 | BOARD no-XFH + Origin evil | 403 | 403 | MATCH |
| B10.1 | `XFH: evil.example@views.example` (userinfo) + Origin evil | 403 | 403 | MATCH |
| B10.2 | same XFH + Origin views | 303 | 303 + cookie | MATCH |
| B10.3 | `XFH: views.example@evil.example` (userinfo reversed) + Origin evil | 403 (veto) | 403 | MATCH |
| B10.4 | `XFH: www.views.example` + Origin views | 303 (www fold inside the veto) | 303 + cookie | MATCH |
| B10.5 | `Origin: http://views.example` (declared base is https) | 303 (scheme never participates) | 303 + cookie | MATCH |
| B10.6 | `XFH: views.example:9999` + Origin views | 303 (the veto's own port elision) | 303 + cookie | MATCH |
| B8.1 | **control** (auth OFF): real `ensemble_sid` cookie + forged XFH evil + Origin evil | 403 | 403 | MATCH |

**Board lane:** the board row **was** cheaply mintable (the `board` root is RESERVED for `view_link`, so the mint went through `ViewPasscodeService.mint("board","index.html")` — the service call, not the tool). The board verify endpoint (`POST /views/public/board/index.html`) routes through the **same** `_public_post` → `_same_host_origin_check` → `_views_trusted_hosts` chain: the forged-XFH re-kill ⇒ 403 there too.

**XFH userinfo:** the forwarded value's *hostname* is what the veto sees (`evil.example@views.example` ⇒ hostname `views.example` ⇒ agrees ⇒ no veto; `views.example@evil.example` ⇒ hostname `evil.example` ⇒ veto ⇒ 403). Either way the forwarded value can only veto — it can never vouch — so the grant in B10.2 still came from the declared anchor.

### 3.7 GROUP C — BOOT 2 (auth ON, audience `https://op.example`, NO declared base)

| # | Probe | Expected | Observed | Verdict |
|---|---|---|---|---|
| C1 | `Origin: https://op.example` | 303 | 303 + cookie | MATCH |
| C2 | `Origin: https://www.op.example` | 303 (www ≡ apex) | 303 + cookie | MATCH |
| C3 | `Origin: https://WWW.OP.EXAMPLE` | 303 (case) | 303 + cookie | MATCH |
| C4 | `Origin: https://op.example.` | (record; **brief said 403**) | **303 + cookie** | RECORD — see D1 |
| C5 | no Origin | 303 (absent-Origin allow) | 303 + cookie | MATCH |
| C6 | `Origin: null`, no Sec-Fetch-Site | 303 (null-allow) | 303 + cookie | MATCH |
| C7 | `Origin: null` + `Sec-Fetch-Site: cross-site` | 403 | 403 | MATCH |
| C8 | `Origin: https://evil.example` | 403 | 403 | MATCH |
| C9 | forged XFH evil + Origin evil + `Host: op.example` | 403 (undeclared front ⇒ forwarded inert) | 403 | MATCH |
| C10 | forged XFH evil + Origin op.example | 303 (grant source = the **audience**) | 303 + cookie | MATCH |
| C11 | `Host: evil.example` (unvouched) + Origin op.example | 303 (audience vouches the Origin) | 303 + cookie | MATCH |
| C12 | `Origin: https://op.example:8443` | (record) | 303 + cookie | RECORD — see D2 |
| C13 | `Host: op.example:8443` + `Origin: https://op.example:8443` | (record) | 303 + cookie | RECORD — see D2 |
| C14 | `Origin: https://[::1]` + `Host: 127.0.0.1:8082` | 403 (the loopback anchor is `127.0.0.1:8082`) | 403 | MATCH |
| C15 | `Origin: https://127.0.0.1:8082` + `Host: 127.0.0.1:8082` | 303 (exact-loopback carve — allowed) | 303 + cookie | MATCH |
| C16 | `Host/Origin: op.example.evil.com` | 403 | 403 | MATCH |

### 3.8 GROUP C2 — session path (R1 session bypass; a valid session is an ALLOWED grant source)

| # | Probe | Expected | Observed | Verdict |
|---|---|---|---|---|
| C17 | valid session + forged XFH evil + Origin evil | 303 (**by-design R1** — the session precedes the origin gate) | 303 + cookie | MATCH |
| C18 | valid session + vouched shape (XFH op.example + Origin op.example) | 303 (**regression guard: the legit proxied shape must grant**) | 303 + cookie | MATCH |
| C19 | valid session + no Origin | 303 | 303 + cookie | MATCH |
| C20 | valid session + `Origin: null` + `Sec-Fetch-Site: cross-site` | (record) | **303 + cookie** | RECORD — the session bypass is total (it never reaches the origin gate); by design ("never origin-checked"), **not** a defect |
| C21 | **garbage** session cookie + forged XFH evil + Origin evil | 403 (falls through to the anonymous chain) | 403 | MATCH |

**Session coverage note:** the session bypass requires the auth service, so with **auth OFF the cookie is inert** — proven by **B8.1** in BOOT 1 (real `ensemble_sid` + forged XFH + evil Origin ⇒ 403). The meaningful session probes therefore ran in BOOT 2 (auth ON).

### 3.9 GROUP E — origin-parser leniency edges (BOOT 2)

| # | Probe | Observed | Note |
|---|---|---|---|
| E1 | `Origin: //op.example` (scheme-less) | **303 + cookie** | the parser's hostname wins |
| E2 | `Origin: https://evil.example@op.example` (userinfo) | **303 + cookie** | the parser's hostname wins |
| E3 | `Origin: https://op.example/some/path` (path on an Origin) | **303 + cookie** | path ignored |
| E4 | E2 + forged XFH evil (undeclared front ⇒ no veto) | **303 + cookie** | grant source = the audience |

**Reachability:** none of these four is a browser-reachable Origin spelling (a browser serialises an Origin as `scheme://host[:port]` — never scheme-less, never with userinfo, never with a path). A non-browser client that can send them would still need the passcode (the origin gate is the CSRF layer, not the passcode control). **Recorded as a parser-laxness note, not a bypass.**

---

## 4. ROWPEEK LEDGER (in-DB `failed_attempts`)

Full ledger: `ROWPEEK-LEDGER.txt` (13 checkpoints, one per cluster). Summary:

| Cluster | `g27fix/rekill2.md` | `board/index.html` |
|---|---|---|
| before-A | 0 | 0 |
| after-A3 (3× re-kill) | **0** | 0 |
| after-A (controls) | 0 | 0 |
| before/after B1 (groups 1+2) | 0 / 0 | 0 |
| before/after B2 (groups 3+4) | 0 / 0 | 0 |
| before/after B3 (group 5) | 0 / 0 | 0 |
| before/after B4 (groups 6+7) | 0 / 0 | 0 |
| before/after B5 (groups 9+10 + session control) | 0 / 0 | 0 |
| before/after C1 (group C) | 0 / 0 | 0 |
| before/after C2 (session path) | 0 / 0 | 0 |
| FINAL (after E run-2) | **4** | 0 |

**The 4 strikes are attributable 1:1 to my own harness slip in group E run-1** (I sent a dummy code instead of the real passcode ⇒ 4 wrong-code compares; see §6). **Every origin-gate rejection in the whole sweep consumed ZERO strikes** — including all 3 re-kills, the board re-kill, and every 403 — which is itself the proof that the gate rejects *before* the compare.

---

## 5. Boot summaries

**BOOT 1 (`boot1`, declared front)** — config: `BOOT-CONFIG-boot1.txt`
* auth **OFF**, **no audience**, `ENSEMBLE_LIVE_VIEWS_EXTERNAL_BASE_URL=https://views.example` (the FIX-7 knob → `live_views.external_base_url` → `LiveViewsService.declared_external_base_url`).
* `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` pinned; `POSTGRES_DB=ensemble_dev` asserted; `FAKE_HOME=/tmp/lvrekill2.WkbRCf/home`; one drill Fernet key (`sha256:cb8cc7bf7996…`).
* Ambient `POSTGRES_*` and `ENSEMBLE_AUTH_*` scrubbed **per-variable** before *and* after sourcing `.env`; `ENSEMBLE_AUTH_DISCOVERY_URL` / `ISSUER` / `EXTERNAL_REDIRECT_BASE` proved absent after the scrub.
* Up on 8082; stopped with a **port-verified** kill (pid = the 8082 listener, cmdline `python -m daemon`, cwd = the worktree); **port 8082 re-verified free**.

**BOOT 2 (`boot2`, auth ON)** — config: `BOOT-CONFIG-boot2.txt`
* auth **ON**, audience `https://op.example`, `ENSEMBLE_AUTH_ISSUER=https://drill.invalid` (drill-fake; the live door.ensem.dev issuer was scrubbed), `ENSEMBLE_AUTH_DISCOVERY_URL` **unset**, `ENSEMBLE_LIVE_VIEWS_EXTERNAL_BASE_URL` **unset** (no declared front ⇒ the forwarded legs stay inert per FIX-1).
* Same fence / same drill key / same dev DB / same sweep pin / same FAKE_HOME.
* Stopped with the same port-verified kill; **port 8082 re-verified free**.

**Drill artefacts minted (dev DB `ensemble_dev` only):** `g27fix/rekill2.md` (generation 1), `board/index.html` (generation 1), and one `ensemble_sid` session row (subject `rekill2-drill-operator`, `ip_subnet=127.0.0.0/24`, **expires 2026-11-09T03:59:40Z** ⇒ self-reaping).

---

## 6. Findings that are NOT escalations (for the council)

1. **D1 / D2 / D3** — see §2. Three brief-vs-implementation divergences, all resolved in favour of the shipped + pinned contract.
2. **Origin-parser leniency** (E1–E4): scheme-less / userinfo-bearing / path-bearing Origin values are accepted against a vouched host. Not browser-reachable ⇒ no CSRF impact. A tightening option (reject an Origin with no scheme or with userinfo) exists if the council wants defence in depth; it is a **behaviour change**, not a bypass fix.
3. **Unparsed `Forwarded` parameter spellings are inert**: the parser only recognises a parameter token starting exactly with `host=` in the first element (e.g. `Forwarded: host = evil.example` — with a space, which RFC 7239 does not permit — is not seen). Consequence: **no veto**, never a grant — the grant still requires the Origin to match the anchor set. No security impact; noted for completeness.
4. **Port + scheme are not part of the vouch** (D2 residual): a portless declaration vouches the name on every port, and the scheme is ignored. Both are pinned behaviours, not regressions.
5. **Session bypass is total** (C20): a valid session grants even with `Origin: null` + `Sec-Fetch-Site: cross-site`, because the bypass precedes the origin gate. This is the documented R1 posture ("never origin-checked") and a valid session is an allowed grant source; recorded so the council can confirm it is intended.

**Harness slip (disclosed):** group E run-1 sent `code=dummy-not-used` instead of the real passcode ⇒ 4 wrong-code compares ⇒ the 200 "Enter passcode" failure page (which still proves the origin gate *allowed* those shapes, since a gate rejection is a 403) and 4 strikes on the drill row. E was **re-run with the correct code** (`E-origin-parser-edges.txt`, 4× 303 + cookie) — the strike counter is NOT reset by a successful verify (`verify()` never writes the DB; only `refresh` resets it), hence the final `failed_attempts=4` on the drill row. The run-1 transcript is preserved as `E-origin-parser-edges-RUN1-dummy-code.txt`.

---

## 7. Artifact index (this directory)

| File | Content |
|---|---|
| `VERDICT-SUMMARY.md` | this document |
| `BOOT-CONFIG-boot1.txt`, `BOOT-CONFIG-boot2.txt` | the two boot shapes (env, key fingerprint, DB assert) |
| `A-rekill.txt` | group A (re-kill ×3 + controls) |
| `B1-xfh-multi.txt` | groups 1+2 (XFH multi-value / multi-LINE) |
| `B2-forwarded-referer.txt` | groups 3+4 (Forwarded variants + Referer) |
| `B3-ports.txt` | group 5 (port forms) |
| `B4-case-dot-idn.txt` | groups 6+7 (case / trailing-dot / IPv6 / IDN) |
| `B5-board-and-parse-edges.txt` | groups 9+10 (board lane, parse edges, session control) |
| `C1-boot2-audience.txt` | group C (apex/www + regression guards + inertness hunt) |
| `C2-boot2-session.txt` | group C2 (session path) |
| `E-origin-parser-edges.txt` | group E run-2 (correct code — the decisive run) |
| `E-origin-parser-edges-RUN1-dummy-code.txt` | group E run-1 (harness slip — disclosed) |
| `ROWPEEK-LEDGER.txt` | 13-checkpoint in-DB strike ledger |
| `boot-boot1.log`, `boot-boot2.log` | daemon boot logs |
| `rk_*.sh`, `rk_*.py` | the reusable harness (lib / boot / stop / mint / session / rowpeek / probe groups) |
| `/tmp/lvrekill2-mirror/` | /tmp mirror of the transcripts + ledger |
| `/tmp/lvrekill2.WkbRCf/` | the fence (FAKE_HOME, data dir, drill roots, state.env with the drill key) |

**No commit was made** — the worktree carries exactly one untracked path (this directory) and `HEAD` is still `4be947eee`. The lane owner owns the landing.
