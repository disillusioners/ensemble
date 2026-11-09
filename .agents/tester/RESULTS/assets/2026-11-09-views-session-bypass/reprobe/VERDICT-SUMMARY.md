# G2.7 FIX RE-PROBE — VERDICT SUMMARY (2026-11-09)

Target: agents-ensemble worktree `/home/nea/ensemble-src-wt-views-session-bypass`
Branch `feature/views-session-bypass` @ tip **6398a29a3** (verified BEFORE any probe; clean tree).
Fix under test: `6398a29a3` — "fix(views): host anchor requires operator vouching (G2.7 rebinding self-match)".
Independent verification (this runner was NOT a replay of council/giter probes; 5 own extras CX1–CX5 added beyond the commissioned matrix).

## Boot shape
- Fenced auth-ON daemon, PORT **8082** only; FAKE_HOME + escape-guard fence `/tmp/lvreprobe.*`; ONE drill Fernet key; sweep `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` pinned; ambient live-door vars (`ENSEMBLE_AUTH_ISSUER/DISCOVERY_URL/EXTERNAL_REDIRECT_BASE`) scrubbed per-variable with post-scrub assert; ambient POSTGRES_* scrubbed per-variable; dev-DB assert `ensemble_dev` passed; port-verify-before-boot (8082 was free; stale g2g8 daemon pid 389019 already gone).
- `ENSEMBLE_AUTH_ALLOWED_ORIGINS=https://operator.example` ONLY — deliberately NOT the drill host ⇒ `127.0.0.1:8082` NOT audience-vouched. NO declared external base ⇒ proxy-front legs (b)/(c) + declared-anchor leg (e) INERT.
- Audience leg (d) live: anchor `(operator.example, portless)`. Loopback carve live: peer 127.0.0.1 + exact names {localhost, 127.0.0.1, ::1}.
- Drill view: root `g27fix`, `reprobe.md`, passcode minted gen 1, locked=no. Correct code on EVERY probe (any observed strike delta would be an anomaly signal; none occurred).

## Verdict table (expected → observed; every probe: status + content-type + Set-Cookie + rowpeek)

| Probe | Shape | Expected | Observed | Verdict |
|---|---|---|---|---|
| A1 | Host: evil.example + Origin: https://evil.example (THE EXPLOIT) | 403 text/html, NO Set-Cookie, 0 strikes | 403 text/html, sc=0, ledger 0 | ✅ DIES |
| A2 | repeat | same | same | ✅ stable |
| A3 | repeat | same | same | ✅ stable |
| B1 | Origin: null, no SFS (cell i) | 303 + grant | 303, sc=1 | ✅ |
| B2 | Origin: null + SFS cross-site | 403 HTML, no cookie | 403 text/html, sc=0 | ✅ |
| B3 | G1-SHAPE: Host evil + Origin null | 303 + grant (null-allow precedes anchors) | 303, sc=1 | ✅ structural confirm |
| C1 | Host/Origin evil:8443 self-match (old pin shape, foreign host) | 403 | 403 text/html | ✅ |
| C2a | evil host + www.evil origin | 403 | 403 | ✅ www-fold only under vouched anchors |
| C2b | www.evil host + evil origin | 403 | 403 | ✅ |
| C3a | sub.evil self-match | 403 | 403 | ✅ |
| C3b | sub.evil host + other.evil origin | 403 | 403 | ✅ |
| C4a | Host EVIL.EXAMPLE + evil origin | 403 (record) | 403 | ✅ vouch check lowercases |
| C4b | evil host + EVIL.EXAMPLE origin | 403 | 403 | ✅ |
| C5a | trailing dot both sides | 403 | 403 | ✅ |
| C5b | trailing dot cross-form | 403 | 403 | ✅ |
| C6a | Host/Origin 127.0.0.1.evil.example (peer IS loopback) | 403 | 403 | ✅ exactness holds from loopback |
| C6b | localhost.evil.example self-match | 403 | 403 | ✅ |
| C6c | octal 0177.0.0.1 self-match | 403 | 403 | ✅ |
| C6d | 0177.0.0.1 host + 127.0.0.1 origin | 403 | 403 | ✅ |
| C7a | localhost host + 127.0.0.1 origin | 403 (names differ) | 403 | ✅ |
| C7b | 127.0.0.1 self-match | GRANT-expected via carve | 303, sc=1 | ✅ carve grant (attribution below) |
| C7c | 127.0.0.2 self-match (loopback RANGE) | 403 | 403 | ✅ no /8 blanket |
| C7d | [::1] self-match | record (carve) | 303, sc=1 | ✅ ::1 exact carve |
| C8a | XFH evil + evil Origin (no declared front) | 403 | 403 | ✅ leg (b) inert |
| C8b | Forwarded: host=evil + evil Origin | 403 | 403 | ✅ leg (c) inert |
| C9a | evil host, ABSENT Origin | GRANT-expected (by design) | 303, sc=1 | ✅ NOT regressed |
| C9b | evil host, EMPTY Origin header | record (=absent) | 303, sc=1 | ✅ |
| C10a | [::1]:8082 self-match | record (carve) | 303, sc=1 | ✅ exact ::1 + ports equal |
| C10b | [::1]:8082 host + [::1] origin | record (port-elision) | 303, sc=1 | ✅ FIX-3 preserved |
| CX1 | LOCALHOST host + localhost origin | record (carve, case) | 303, sc=1 | ✅ vouch case-insensitive; consistent w/ C4a |
| CX2 | ::ffff:127.0.0.1 self-match | 403 (peer-normalization must not leak into name) | 403 | ✅ own extra |
| CX3 | 127.0.0.1 host + evil origin (carve asymmetry) | 403 | 403 | ✅ own extra |
| CX4 | evil host + Referer: https://evil.example/x, no Origin | 403 (Referer judged by vouched anchors) | 403 | ✅ own extra |
| CX5 | 127.0.0.1:8443 self-match (carve + port) | record (carve) | 303, sc=1 | ✅ loopback dev on any port = carve by-design |
| D1 | Origin: https://operator.example (audience leg) | 303 + grant | 303, sc=1 | ✅ regression guard |
| D2 | no Origin / no SFS plain flow | 303 + grant | 303, sc=1 | ✅ |
| D3 | Origin: https://operator.example:443 (port-elision on vouched anchor) | 303 + grant | 303, sc=1 | ✅ |
| E2E | grant minted → GET with cookie | 200 + view body | 200 text/html + SECRET-CONTENT-REPROBE-marker | ✅ grants are REAL |

39 requests total (38 POSTs + 1 granted GET), paced ≥14s (bucket: capacity 5, refill 5/min — zero 429s observed).

## failed_attempts ledger
22 rowpeek checkpoints (baseline + after every cluster): **failed_attempts=0 at EVERY checkpoint; locked=False; generation=1 throughout.** No probe ever consumed a strike; all rejects sit at CSRF gate #4, before compare. Full ledger: `ROWPEEK-LEDGER.txt`.

## DEFECT determination
**NO DEFECT.** Every grant observed is attributable to exactly one of:
1. the loopback carve — exact loopback spelling + loopback TCP peer (C7b/C7d/C10a/C10b/CX1/CX5) — soundness pinned by the negatives (127.0.0.2, both lookalikes, octal, mapped-v4, cross-name all 403 EVEN THOUGH the peer is loopback);
2. the absent/null-Origin branches that run BEFORE the anchor universe (C9a/C9b/B1/B3) — R2 by-design, unchanged by the fix (B3 proves the ordering directly);
3. the operator audience leg (D1/D3).
**Zero grants issued from a non-vouched, non-loopback-exact source.** The attacker-controlled self-match class (any Host/Origin spelling pair, ports, case, www, trailing dot, subdomains, siblings, XFH/Forwarded revival, Referer fallback) is dead: 22/22 reject probes returned 403 text/html with no Set-Cookie.

## Residual observations (not defects; recorded for the class-registry)
- **Absent-Origin with attacker Host still grants** (C9a): R2 by-design — anchors gate PRESENT origins only; a non-browser client can always omit Origin. Backstops = bucket + strike lock. Flagged as the widest remaining surface in this class; fix intentionally preserved it (regression would have been a defect).
- **Carve grants on arbitrary ports** (CX5, and portless↔ported elision C10b): exact-name + peer-gated, so only a loopback-local process benefits; by-design dev posture.
- **Carve vouch is case-insensitive** (CX1 grants via LOCALHOST): consistent with the auth-lane mirror and the C4a host-case posture — semantically fine, recorded.

## Artifacts
- `A-exploit-dies.txt` (3), `B-cell-i-g1shape.txt` (3), `C1-C6-hunt.txt` (13), `C7-CX-hunt.txt` (15), `D-regression-guard.txt` (3), `E2E-grant-works.txt` (2), `ROWPEEK-LEDGER.txt` (22 checkpoints), `BOOT-CONFIG.txt`, `MINT.txt` — this dir + `/tmp/lvreprobe-evidence/` mirror.
- Fence: `/tmp/lvreprobe.*` (FAKE_HOME inside; ONE drill key; boot-pid recorded).

## Cleanup
Daemon pid (recorded in fence boot-pid) killed after PORT-IDENTITY verification (listening socket 8082 + cmdline/worktree match) at run end; 8082 freed. 8083/8088 never touched.

## CLEANUP — 2026-11-09T00:20:37Z
daemon pid 417275 (recorded in fence boot-pid; identity verified: cmdline '.venv/bin/python -m daemon' + cwd=worktree + ss socket-owner of 8082) SIGTERM-terminated; 8082 FREE after; 8083/8088 never bound by this lane.
