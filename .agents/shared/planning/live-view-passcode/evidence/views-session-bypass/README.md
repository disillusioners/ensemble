# Evidence — views-session-bypass lane (live-views passcode origin gate)

**Lane:** live-views passcode — session bypass, navigation-correct origin gate,
never-JSON-on-human-pages, host vouching (G2.7), forwarded-anchor class closure
(FIX-10).
**Branch tip at commit time:** `523c5eed7` (docs/evidence commits follow on top).
**Gate code is FROZEN** — this directory holds evidence and harness only; no
`daemon/` or `tests/` file is touched by the closure commits.

## What is here

| Path | What |
|---|---|
| `MUTATION-MATRIX-ACCURATE.md` | the authoritative discrimination matrix (canary-asserted) + the loop-2 record correction |
| `mutation-harness/run_matrix.py` | the canary-asserted matrix runner (load canary / call canary / cascade detection) |
| `mutation-harness/mut_*.py`, `nolegs_plugin.py`, `ungate_plugin.py` | the mutant plugins (pytest `-p` plugins that monkeypatch the gate seam) |
| `mutation-harness/non-evidence/` | the **broken-mutant (NameError-cascade) artifacts** — committed for completeness, EXCLUDED from the matrix (anomaly `95b19c03`) |
| `mutation-harness/*_probe.py`, `probe_redundancy.py` | the behavioural probes (G2.7 / FIX-7 / FIX-10 redundancy probe) |
| `mutation-harness/gen_mut_fwd.py` | the generator for the pre-FIX-10 blob plugin (also the source of the broken-cascade artifact — see the matrix note) |

Everything runs **from the branch** — no `/tmp` dependency for the invocation. The
harness derives the repository root by walking up to the directory that contains
`daemon/routers`.

## Exact commands

Run from the repository root of the worktree (or any clone of this branch):

```bash
# 0) the env scrubs — per-variable, NEVER the `env -u POSTGRES_*` glob form
#    (bash does not glob env names: the glob idiom is a silent NO-OP and an
#    ambient POSTGRES_DB can leak into a "fenced" run).
env -u ENSEMBLE_AUTH_ALLOWED_ORIGINS -u ENSEMBLE_AUTH_EXTERNAL_REDIRECT_BASE \
    -u ENSEMBLE_AUTH_ISSUER -u ENSEMBLE_AUTH_DISCOVERY_URL \
    -u ENSEMBLE_AUTH_CLIENT_ID -u ENSEMBLE_AUTH_CLIENT_SECRET \
    -u ENSEMBLE_AUTH_ENABLED \
    -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD \
    -u POSTGRES_HOST -u POSTGRES_PORT \
  .venv/bin/python -m pytest \
    tests/unit/routers/test_live_views_session_bypass.py -q --timeout 120
# EXPECT: 98 passed (the baseline the matrix is measured against).

# 1) the whole matrix, canary-asserted
HARNESS=.agents/shared/planning/live-view-passcode/evidence/views-session-bypass/mutation-harness
env -u ENSEMBLE_AUTH_ALLOWED_ORIGINS -u ENSEMBLE_AUTH_EXTERNAL_REDIRECT_BASE \
    -u ENSEMBLE_AUTH_ISSUER -u ENSEMBLE_AUTH_DISCOVERY_URL \
    -u ENSEMBLE_AUTH_CLIENT_ID -u ENSEMBLE_AUTH_CLIENT_SECRET \
    -u ENSEMBLE_AUTH_ENABLED \
    -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD \
    -u POSTGRES_HOST -u POSTGRES_PORT \
  .venv/bin/python "$HARNESS/run_matrix.py"
# The runner sets PYTHONPATH to the harness dir (+ non-evidence dir) itself,
# runs the lane file with the plugin installed, and prints:
#   plugin | F | P | load | call | cascade
# A count is EVIDENCE only when cascade == no (see the matrix doc).
# RUNTIME: ~5 s per plugin (the full 29-plugin sweep is ~3 min) — chunk it
# if your invocation has a 2-minute cap, e.g.
#   ... run_matrix.py mut_fwd_canary mut_fwd_unconstrained mut_fwd_54 mut_fwd_revert2
#   ... run_matrix.py mut_fwd_onlyfn mut_veto_missing mut_veto_always mut_rawhost

# 2) a single mutant (the plugin invocation pattern)
PYTHONPATH="$PWD/$HARNESS" .venv/bin/python -m pytest \
  tests/unit/routers/test_live_views_session_bypass.py -q --timeout 120 \
  -p mut_fwd_canary
# EXPECT: 8 failed, 90 passed, and the plugin prints BOTH
#   "[mut_fwd_canary] plugin loaded"        (load canary)
#   "[CANARY] pre-FIX-10 _views_trusted_hosts IS INSTALLED AND CALLED" (call canary)

# 3) the FIX-10 redundancy probe (the analysis that chose the mechanism)
env -u ENSEMBLE_AUTH_ALLOWED_ORIGINS -u ENSEMBLE_AUTH_EXTERNAL_REDIRECT_BASE \
    -u ENSEMBLE_AUTH_ISSUER -u ENSEMBLE_AUTH_DISCOVERY_URL \
    -u ENSEMBLE_AUTH_CLIENT_ID -u ENSEMBLE_AUTH_CLIENT_SECRET \
    -u ENSEMBLE_AUTH_ENABLED \
  .venv/bin/python "$HARNESS/probe_redundancy.py"
# EXPECT: "forwarded-ONLY addns: []" for every declared fixture ⇒ legs (b)/(c)
# are provable no-ops (the mechanism: DELETION + the agreement veto).
```

**Plugin invocation pattern:** `-p <module>` — the plugin must be importable, hence
`PYTHONPATH` must include the harness directory (the runner does this for you).
The plugins that restore a historical implementation load a **blob** of the old
file as a MODULE (so its own module-level helpers resolve) — that is why the
faithful variants measure a meaningful count while a function-only transplant
collapses into a `NameError` cascade (excluded, see the matrix doc).

**Env scrubs:** the ambient shell of a live operator exports `ENSEMBLE_AUTH_*`
(live discovery/issuer/client). Any `AuthConfig` construction in the test fixtures
must not see them — the discovery-vs-issuer origin validator refuses the mix and the
fixture aborts at setup. Scrub them **per-variable** (the `env -u POSTGRES_*` glob
form is a silent no-op).

## Anomaly index

| Anomaly | Subject | Status |
|---|---|---|
| `2c719281` | G2.7 — DNS-rebinding self-match (`Host: evil.example` + `Origin: https://evil.example` ⇒ 303 + full grant) | **CLOSED** (FIX-9, commit `6398a29a3`); the mutant `mut_rawhost` (17F) pins it |
| `6c543ecc` | same-class bypass — in a declared front the first `X-Forwarded-Host` value anchored UNCONSTRAINED (forged `XFH: evil.example` ⇒ 303 + full grant) | **CLOSED** (FIX-10, commit `ac5f024fc`); the faithful mutants (8F) pin it |
| `95b19c03` | the broken-mutant (NameError-cascade) artifacts that inflated the earlier reported counts | **EXCLUDED from the matrix** (non-evidence; labelled in `mutation-harness/non-evidence/`) |

## Record corrections carried by this evidence

1. **The loop-2 count was wrong.** The earlier report quoted
   `mut_fwd_unconstrained ⇒ 54 failed`; the faithful variants measure **8F** — the
   54 was a NameError-cascade artifact (see `MUTATION-MATRIX-ACCURATE.md`).
2. **"Comparison primitives byte-identical" was wrong.** `_www_equivalent` DID
   change in loop 2 — a benign symmetric fold on both operands (via the new
   `_views_host_key`: lowercase + trailing-dot strip, applied before the single
   `www.` strip). FIVE primitives are byte-identical: `_same_host_origin_check`,
   `_host_matches`, `_origin_url_host_port`, `_views_exact_loopback_name`,
   `_authority_host_port`. The same correction is in the deployment runbook §1.1.2.

## Operator-facing contract (pointer)

The lane's operator contract — declared fronts, the forwarded-VETO, the multi-vhost
strict contract, and the ceremony precondition (pin Envoy's XFH BEFORE declaring the
front) — lives in **`docs/runbooks/live-view-passcode-deployment.md`**
(§"CEREMONY PRECONDITION", §1.1, §1.1.1, §1.1.2).
