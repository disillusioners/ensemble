# Accurate mutation-discrimination matrix — views-session-bypass lane

**Scope:** the live-views passcode origin gate (`daemon/routers/live_views.py`).
**Baseline:** lane file `tests/unit/routers/test_live_views_session_bypass.py` —
**98 passed** at tip `523c5eed7` (the commit that added the K8 multi-vhost pin).
**Harness:** `mutation-harness/run_matrix.py` (canary-asserted) — the numbers below
were produced **from this branch**, not from `/tmp` (see `README.md` for the exact
invocation).

## How to read a number

The runner asserts three things per mutant **before** the count is trusted:

| Column | Meaning |
|---|---|
| **load canary** | the plugin printed its "plugin loaded" marker — the plugin really loaded |
| **call canary** | the plugin printed its FIRST-CALL marker — the patched seam was really exercised (not a stale import / no-op plugin) |
| **cascade** | the run shows `NameError` / 5xx responses — the mutant is BROKEN (an artifact), **not** discriminating power |

A failure count is evidence only when the cascade column reads `no`. The `F` counts
below are the lane-file failure counts with the plugin installed.

## The FIX-10 class-closure mutants (the ones that matter)

| Mutant | What it breaks | F | load | call | cascade | Discriminating pins (representative) |
|---|---|---|---|---|---|---|
| `mut_fwd_canary` | pre-FIX-10 blob (`6398a29a3`) — forwarded value anchors again | **8** | YES | YES | no | K1, K2, K4, K5, K6×2, K8, `trusted_hosts_unit_unset_vs_set` (7 prior + **K8**) |
| `mut_fwd_unconstrained` | same | 8 | – | – | no | same 8 |
| `mut_fwd_54` | same | 8 | – | – | no | same 8 |
| `mut_fwd_revert2` | same (+ old `_www_equivalent`) | 8 | – | – | no | same 8 |
| `mut_fwd_full` | same (whole namespace swapped) | 8 | – | – | no | same 8 |
| `mut_fwd_module` | same (`sys.modules` replaced) | 8 | – | – | no | same 8 |
| `mut_fwd_strict` | same (namespace + helpers stripped) | 8 | – | – | no | same 8 |
| `mut_fwd_onlyfn` | **surgical** — forwarded anchors re-added to the current function | **6** | – | – | no | `trusted_hosts_unit_unset_vs_set`, K1, K5, K6×2 (5 prior + **K8**) |
| `mut_veto_missing` | the VETO is deleted (forwarded value anchors again) | **17** | – | – | no | the FIX-10 contract set (16 prior + **K8**) |
| `mut_veto_always` | the veto always fires (over-broad) | **28** | – | – | no | degradation + topology pins (27 prior + **K8**) |

## The earlier-lane mutants (kept for the record)

| Mutant | What it breaks | F | cascade |
|---|---|---|---|
| `mut_ungate` / `ungate_plugin` | iteration-0 UNGATED legs (b)/(c) | 23 | no |
| `mut_rawhost` | unconditional raw-Host anchor (the G2.7 shape) | 17 | no |
| `mut_fwdtail` | all `Forwarded` elements trusted | 22 | no |
| `nolegs_plugin` | legs (b)+(c) removed | 17 | no |
| `mut_xfh_only` / `mut_xfh_only_v2` | leg (b) removed / re-shaped | 17 / 16 | no |
| `mut_novouch` / `mut_nolegd` | the vouch / the declared-leg removed | 10 / 19 | no |
| `mut_reject_all_null` | every `Origin: null` rejected | 9 | no |
| `mut_no_declared_anchor` | the declared-base anchor removed | 6 | no |
| `mut_www` / `mut_www2` / `mut_suffix` / `mut_startswith` | www-fold / suffix / prefix abuse | 2 each | no |
| `mut_port` / `mut_originport` / `mut_secfetch` | malformed-port mapping / the Sec-Fetch-Site discriminator | 1 each | no |

**Every non-cascade mutant discriminates; no mutant passes clean.**

## EXCLUDED — broken-mutant artifacts (NOT evidence)

`mut_fwd_nameerror` (**35F**) and `mut_fwd_broken_cascade` (**55F**) are
**NameError cascades, not discriminating power** (anomaly `95b19c03`): the mutant
embeds only the old function into a namespace where its module-level helpers are
unresolved, so the request path raises instead of asserting. They are committed for
completeness under `mutation-harness/non-evidence/` and are **excluded from the
table above**.

> **Correction of the record:** an earlier lane report quoted
> `mut_fwd_unconstrained ⇒ 54 failed`. That number was produced by exactly this
> broken-generator cascade. The **faithful** variants (the pre-FIX-10 blob loaded as
> a MODULE, so it carries its own helpers) measure **8F** — the discriminating set
> is K1/K2/K4 + the trust-set unit pin (+ K8 since the loop-2 cleanup). The
> promote notes must carry the accurate numbers from this table.

## Comparison primitives — accurate status (loop-2 correction)

FIVE comparison primitives are **byte-identical** to the pre-lane implementation:
`_same_host_origin_check`, `_host_matches`, `_origin_url_host_port`,
`_views_exact_loopback_name`, `_authority_host_port`.

**`_www_equivalent` DID change in loop 2** — an earlier lane report claiming
"comparison primitives byte-identical" for all of them was wrong. The change is a
benign **symmetric** fold on **both** operands: it routes through the new
`_views_host_key` (lowercase + trailing-dot strip) before the single `www.` strip.
Symmetric ⇒ neither operand is treated differently; the only semantic effect is that
the FQDN trailing-dot form (`example.com.`) now folds like `example.com`.
