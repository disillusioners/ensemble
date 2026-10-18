# E2E — auth parity, State-B shape (commission item 5) — live-view passcode

Worktree: `/home/nea/ensemble-src-wt-liveview-passcode` (branch `feature/live-view-passcode` @ 1121f2604)
Executed: 2026-10-18 ~04:49–05:03 UTC · ports 8085 (drill-owned, released) · PG ensemble_dev (asserted) · drill-only Fernet key
Fencing: mktemp `/tmp/lv-parity-s1.*` / `/tmp/lv-parity-s2.*`, escape-guarded deletion, verified-port kill-own-group only, 8088 untouched. No fence residue, no stray pids, worktree dirt = pre-existing `install-audit.jsonl` + this evidence dir only.

## RESULTS

| Stage probe | Result | Evidence |
|---|---|---|
| S1a fixture `parityroot1/hello.md` = `# parity hello\n` (15 B) | created in fence | `s1-parity-drill.sh` |
| S1b GET 200 / GET 404 / HEAD 200 direct lane | captured | `s1-get200-hello.*`, `s1-get404-absent.*`, `s1-head200-hello.*` |
| S1c golden comparison | **PASS** (6/6 envelopes) | `s1-golden-parity-report.txt`, `s1-envelopes-serialized.txt` |
| S1d unminted PUBLIC lane → uniform 404 | **PASS** (both paths, no location header) | `s1-s1d-public-*.transcript.txt` |
| S2a direct lane unauth → 302 `/api/auth/login?return_to=…` | **PASS** | `s2-s2a-direct-hello.*` |
| S2b public lane unauth: unminted 404 uniform / minted 200 interstitial | **PASS** (State-B shape) | `s2-run1-archive/s2-s2b-pre-*`, `s2-s2b-post-public-hello.*` |
| S2c mint → wrong(200) → plain(secure-context page) → XFP-https(303 + cookie) → GET-with-cookie(200 view bytes) | **PASS** (all 5 cookie attrs) | `s2-s2c-*`, `s2-mint.txt` |
| S2d unminted public path auth-ON → uniform daemon 404 (pre+post mint) | **PASS** | `s2-s2d-*` |
| S2e control `/api/instances` unauth → 302 login | **PASS** | `s2-s2e-api-instances.*` |

Full verdicts: `s1-golden-parity-report.txt` + `s2-report.txt`.

## Golden parity table (Stage 1)

| envelope | byte-identical | first divergence | classification |
|---|---|---|---|
| get404-absent vs golden `GET 404 missing` | YES* | — (transport `date`,`server` only) | PASS-WITH-NOTE |
| page200 (golden-clone body `<h1>Landing</h1>`) vs `GET 200 landing.html` | YES* | — (transport only) | PASS-WITH-NOTE |
| get200-hello (mandated .md fixture) | YES* | wrapper-class headers `content-security-policy`, `referrer-policy` | PASS-WITH-NOTE (fixture-dependent; proven base behavior) |
| head200-hello (mandated .md fixture) | YES* | same wrapper-class headers | PASS-WITH-NOTE (fixture-dependent; HEAD length 3303 == GET wrapper body) |
| s1d public-lane unminted ×2 | YES* | — | PASS-WITH-NOTE |

YES* = byte-equal on all golden-relevant fields (status + every golden header name/value + body where content-independent) modulo transport-added (`date`/`server`) and proven-base-identical wrapper-class headers. Wrapper provenance: `_render_markdown_response` (3221 B) + `_HARDENING_HEADERS` (125 B) **AST-byte-identical between git 859e41193 (golden base) and HEAD** — see `s1-wrapper-provenance.txt`. CSP nonce is per-request nondeterministic by design (present in both GET and HEAD pairing; HEAD content-length 3303 == GET body 3303).

## Mint seam exercised

`ViewPasscodeService.mint` → `ViewPasscodeRotationRequired` (row from run-1 under a rotated drill key) → `refresh` (documented recovery), generation 1→2, passcode re-displayed. **Seam noted:** the agent-facing `view_link` tool (`daemon/tools/live_views.py:113`) is instance-bound (needs a live InstanceManager); the drill calls the SAME service methods view_link delegates to (`passcode_service.mint/refresh`, `daemon/services/view_passcode_service.py:1132/1384`) with actor `s2-state-b-drill`.

## Boot-log corroboration (s2-boot.log)

- `derived public_path_set (18): [... '/views/public/{root_name}/{rel_path:path}' ...]` — code-seeded prefix tag landed (ADR D3; `anonymous_view_paths` unset).
- `[LiveViews] passcode substrate ready: strike_ceiling=10 grant_ttl=86400s rate_limit=5/5-per-min`
- `[OAuthClient] discovery attempt N failed: transport error: ConnectError` — WARN-not-fatal (drill.invalid issuer), boot unblocked.
- `[AuthAudit] event=request_denied_302 path=/views/parityroot1/hello.md` — direct lane default-deny audit.

## Operational notes (for future drill authors)

- **Ambient `PORT=9797` poisons worktree boots**: `config.yaml:111` substitutes `port: ${PORT:-8088}` and agent shells descend from the live daemon (whose env carries `PORT=9797`). Any fenced boot MUST `export PORT=<drill port>` (and `DAEMON_PORT` for code reading it directly). S1 attempt #3 bound-attempted 9797 because of this; fixed in both scripts.
- `POSTGRES_DB == ensemble_dev` pre-boot assert fired correctly on an early mis-pathed run (ambient `POSTGRES_DB=ensemble_prod` would otherwise have been used).
- Ambient `computer-use` MCP warm-up ERRORs in boot logs are pre-existing environment noise, unrelated to live views.
