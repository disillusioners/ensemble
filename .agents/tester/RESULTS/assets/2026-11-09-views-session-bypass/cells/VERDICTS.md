# G2.7 FIX DEGRADATION CELLS (ii)(iii)(iv) — VERDICTS (2026-11-09)

Fix under test: `6398a29a3` (tip, feature/views-session-bypass) — host anchor
requires operator vouching (audience ∪ declared-base hosts ∨ loopback carve).
Harness: adapted from committed g2g8 assets (boot.sh/lib.sh/mint_views.py)
+ 2026-10-18 rowpeek ledger reader. Runtime ≈ 10 min of drill time
(627s wall incl. harness debug loop); 3 sequential boots, port **8083**
only, full cleanup between (port-verify-before-kill; 8083 FREE at end).

## Boot shapes + disclosures

| Cell | Auth | Declared base | Bind | Daemon pid (now dead) |
|---|---|---|---|---|
| (ii) | OFF (`ENSEMBLE_AUTH_ENABLED=false`) | **`ENSEMBLE_LIVE_VIEWS_EXTERNAL_BASE_URL=https://views.example`** | 127.0.0.1:8083 | 417996 |
| (iii) | OFF | **none** (unset) | 127.0.0.1:8083 | 418888 |
| (iv) | OFF | **none** (unset) | **192.168.1.151:8083 interface bind ONLY** (ss-asserted, no wildcard) | 420156 |

**Declared-base knob used (DISCLOSED):** `ENSEMBLE_LIVE_VIEWS_EXTERNAL_BASE_URL`
— the env form of `live_views.external_base_url`, read through
`LiveViewsService.declared_external_base_url` (FIX-7 source in
`daemon/routers/live_views.py::_views_trusted_hosts`). The auth lane's
`live_view_external_base_url_fallback` copy (api.py:2937) is written ONLY
inside `if config.auth.enabled` ⇒ INERT in these auth-OFF shapes. The
rejected candidate `ENSEMBLE_AUTH_EXTERNAL_REDIRECT_BASE` (a) needs auth ON,
(b) also fakes redirect/audience values — not used; ambient live values
(door.ensem.dev) scrubbed per-variable on every shell.

Safety envelope: dev-DB assert passed (`ensemble_dev`); ambient
`POSTGRES_*` / `ENSEMBLE_AUTH_*` scrubbed per-variable; drill-only auth env;
`ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` exported on every boot;
FAKE_HOME inside the mktemp fence (`/tmp/lvcells.rsYQp7`) with escape
guard; ONE drill Fernet key; browser Accept + `X-Forwarded-Proto: https`
on grant-expecting POSTs; ≥14s pacing (bucket cap 5 / refill 5/min);
`.venv/bin/python`; no Docker; no live contact.

## CELL (ii) — auth-OFF + declared base grants

| Probe | Expected | Observed | Verdict |
|---|---|---|---|
| p1 `Host: views.example` + `Origin: https://views.example` | 303 + grant | **303 + grant** (Set-Cookie view_grant) | ✅ MATCH |
| p2 `Host: views.example` + `Origin: https://www.views.example` | 303 + grant (www≡apex on vouched anchor) | **303 + grant** | ✅ MATCH |
| p3 `Host: views.example` + `Origin: https://evil.example` | 403, zero strikes | **403**, failed_attempts=0 | ✅ MATCH |
| p4 `Host: internal-svc:8079` + `XFH: views.example` + `Origin: https://views.example` | grant (declared front ⇒ XFH leg active, matching declared) | **303 + grant** | ✅ MATCH |
| p5 `Host: internal-svc:8079` + `XFH: evil.example` + `Origin: https://evil.example` | 403 ("an XFH value ≠ declared base creates no vouch") | **303 + FULL view_grant** | ❌ **MISMATCH — BYPASS FINDING** |

Ledger: rowpeek before p1 `failed_attempts=0 gen=1` → after p5
`failed_attempts=0 gen=1` (all 403s pre-compare; grants don't strike).

### ⚠ FINDING (cell ii p5) — forged-forwarded-host grant under a declared front
With ANY declared proxy front present, leg (b) appends the FIRST
`X-Forwarded-Host` value to the trust-anchor set **without requiring it to
equal the declared front** (`_views_trusted_hosts`, live_views.py ~:1213).
`XFH: evil.example` + `Origin: https://evil.example` then matches its own
forged anchor ⇒ 303 + full grant cookie (transcript cell2-transcript.txt,
full Set-Cookie captured). An attacker with direct daemon reachability
bypasses the origin gate whenever the operator has declared a front. The
unit tests pin `XFH:evil` rejected only in the UNDECLARED state
(`test_echoed_xfh_rejected_when_proxy_front_undeclared`); the declared-state
mismatched-XFH case is unpinned. Suggested contract: gate leg (b)/(c)
anchors on `_host_matches(xfh_anchor, declared_anchor)` (value must BE the
declared front), or accept only the declared value. This is the G2-family
self-anchoring class re-entering through the forwarded leg — recommend
filing as the loop's next blocker candidate.

## CELL (iii) — unconfigured loopback carve (auth-OFF, NO declared base)

| Probe | Expected | Observed | Verdict |
|---|---|---|---|
| p1 `Host: 127.0.0.1:8083` + `Origin: https://127.0.0.1:8083` (peer=loopback) | 303 + grant (carve) | **303 + grant** | ✅ MATCH |
| p2 `Host: localhost:8083` + `Origin: https://localhost:8083` | grant if `localhost` accepted | **303 + grant** | ✅ MATCH |
| p3 `Host: 127.0.0.1.evil.example` self-match | 403, zero strikes | **403**, 0 strikes | ✅ MATCH |
| p4 `Host: localhost.evil.example` | 403, zero strikes | **403**, 0 strikes | ✅ MATCH |
| p5 `Host: 127.0.0.2:8083` self-match | 403 (range ≠ canonical; record actual) | **403**, 0 strikes — actual: 127.0.0.2 is NOT an accepted loopback spelling | ✅ MATCH |
| p6 cross-name `Host: localhost:8083` + `Origin: https://127.0.0.1:8083` | 403, zero strikes | **403**, 0 strikes | ✅ MATCH |

Loopback carve accepted-name record (TCP-proven): **`127.0.0.1` ✅ ,
`localhost` ✅ ; `::1` code-accepted (`_views_exact_loopback_name`) but not
TCP-reachable in this drill (IPv4-only bind)**; `127.0.0.2` ❌ (the /8
blanket is correctly NOT honored). Ledger: 0 strikes throughout (same PG
row as cell ii; before-shot = rowpeek-cell2-after.txt).

## CELL (iv) — unconfigured NON-loopback peer (bind 192.168.1.151)

| Probe | Expected | Observed | Verdict |
|---|---|---|---|
| p1 `Host: 192.168.1.151:8083` + `Origin: https://192.168.1.151:8083` + correct code | **403 AND failed_attempts == 0** (origin gate short-circuits BEFORE strike counting) | **403, failed_attempts=0** — equality PROVEN | ✅ MATCH |
| p2 no Origin header | 303 + grant (R2 absent-Origin fail-open preserved) | **303 + grant** | ✅ MATCH |
| p3 `Origin: null` (no Sec-Fetch-Site) | 303 + grant (R2 null-Origin fail-open preserved) | **303 + grant** | ✅ MATCH |

The p1 equality is the cell's point: under NO vouching configuration a
same-origin POST with a REAL Origin is rejected AND never reaches the
passcode compare — no strike, no lock progress. The degradation contract
docstring holds verbatim.

## Rowpeek failed_attempts ledger (view cellroot/matrix-a.md, gen=1)

| Checkpoint | failed_attempts | locked | generation |
|---|---|---|---|
| cell ii before | 0 | False | 1 |
| cell ii after (5 probes: 3 grants, 2 403s) | **0** | False | 1 |
| cell iii after (6 probes: 2 grants, 4 403s) | **0** | False | 1 |
| cell iv before | 0 | False | 1 |
| cell iv after (3 probes: 2 grants, 1 403) | **0** | False | 1 |

**Zero strikes across all 14 verify POSTs — every 403 pre-empts compare; no
defect grants from non-vouched sources in cells (iii)/(iv).** The ONLY
grant from a non-vouched source is cell (ii) p5 (forged XFH under a
declared front) — full transcript in cell2-transcript.txt.

## Cleanup record

Three boots, each killed via cleanup.sh (ss port-verify-before-kill):
pids 417996, 418888, 420156 — all TERM'd cleanly, 8083 FREE after each and
at end. No wildcard bind ever observed (cell iv ss-asserted). Fence
/tmp/lvcells.rsYQp7 left in place (drill key + view fixtures; inside /tmp,
escaped by guard).

## Files

- boot_cell{2,3,4}.sh / probes_cell{2,3,4}.sh / lib.sh / mint_cells.py /
  mint_run_cells.sh / rowpeek_cells.py / cleanup.sh — the harness
- boot-cell{2,3,4}.env.txt (shape disclosures, key redacted) /
  bind-cell{2,3,4}.txt (ss) / boot-cell{2,3,4}.log
- cell{2,3,4}-transcript.txt (full curl/headers/bodies) +
  cell{2,3,4}-verdicts.txt (status lines)
- rowpeek-cell{2,3,4}-{before,after}.txt — the ledger
- mint-output.txt — view mint (passcode redacted here; live value only in
  the transcript POST lines and the fence)
- Mirror: /tmp/cells-evidence/
