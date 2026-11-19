# POK Dual-Key Family Census — 2026-11-18 (corrected family-key blast-radius map)

Point-in-time, script-generated map of how the POK family key changes when the
**single-token** family pattern is replaced by the **corrected multi-token** pattern.
It answers one question for the PM: *which families would newly hit the
`POK_FAMILY_MAX` refusal if the corrected key were adopted, and which are unaffected.*

Sibling artifacts in this directory (`family-cap-consolidation-2026-11-18.md`,
`census-active-2026-11-18.md` / `.json`, `phase4-recensus-2026-11-18.md`,
`cluster-proposals-2026-11-18.md`) were read for convention and were NOT modified.

> **LANDED-STATE SOURCE ANCHORS (fix iteration 2).** The source anchors quoted in §0.1 / §3 / §4 of this artifact were read BEFORE the fix landed (they cite the single-token pattern at `models.py:131`). The canonical POST-landing anchors are: `daemon/repositories/pok/models.py:132` (`POK_FAMILY_SLUG_PATTERN` — multi-token); `daemon/services/pok_service.py:470-531` (the family-admission block + the count-aware `pok_family_full` refusal — the quoted `470-493` is that block's PRE-cure range); `daemon/repositories/pok/repository.py:221-253` (`count_active_in_family` — the quoted `235-241` is the statement body INSIDE that method, shifted by the docstring extension); and the render side `daemon/services/pok_injection.py:564-583` (`render_pok_block` — the quoted `repository.py:570-578` does NOT resolve: `repository.py` is 487 lines; `:570-578` is the render function in `pok_injection.py`).

## 0. Provenance & method

- **generated_at (UTC):** 2026-11-18T21:12:38Z
- **source snapshot:** `/tmp/pok_census_raw_20261118.json`
- **snapshot sha256:** `d8a6e80bbc417530ac6d0e1ea8ba454e6fe3314b7a49e0e02b041054360b8647`
- **snapshot size:** 362415 bytes
- **captured_at (store):** 2026-11-18T20:57:11Z (source field: `2026-11-18T20:57:11.112284+00:00`)
- **source database / table:** `ensemble_prod` / `project_operating_knowledge` (read-only: `PGOPTIONS default_transaction_read_only=on`)
- **row count:** 506 ACTIVE rows across 3 projects; per-row `content_sha256` pre-verified 506/506
- **no body text in the snapshot:** the snapshot carries metadata only (slug, id, timestamps, `content_sha256`, `body_len`); no POK body was read, so no body is reproduced here.
- **method:** script-generated (python3 stdlib) from the read-only snapshot — no DB access, no network, no `pok_*` tool calls. Every id and slug below is copied machine-side out of the snapshot JSON; **no id/slug was retyped by hand**.
- **generator scratch dir (uncommitted, audit):** `/tmp/pok-dualkey-3121314`
- **no live systems touched:** this lane performed no POK writes, no git push, and no source-code change; the single repo write is the commit of this artifact.

### 0.1 The two views

| view | family-key regex | source |
|---|---|---|
| **CURRENT** | `^([a-z][a-z0-9]*)-(\d{2})-(.+)$` | live pattern — `daemon/repositories/pok/models.py:131` (`POK_FAMILY_SLUG_PATTERN`), consumed by `daemon/services/pok_service.py:129` (`POK_FAMILY_RE`), `daemon/services/pok_injection.py:139` |
| **CORRECTED** | `^([a-z][a-z0-9]*(?:-[a-z][a-z0-9]*)*)-(\d{2})-(.+)$` | the documented multi-token intent (maximal run of letter-led `[a-z][a-z0-9]*` tokens before the leftmost `-NN-` ordinal) |

Bound + gate semantics (read from source, unchanged by this lane):

- `daemon/services/pok_service.py:117` — `POK_FAMILY_MAX = 3`.
- `daemon/services/pok_service.py:471-479` — on admission, `family = _slug_family(slug)`; if the family is not `None` and `count_active_in_family(project_id, family) >= POK_FAMILY_MAX` the write is refused with `pok_family_full`.
- `daemon/repositories/pok/repository.py:221-229` — `count_active_in_family` counts ACTIVE rows whose slug `startswith(f"{family}-")`, i.e. **unnumbered prefix rows count toward the bound**.
- `daemon/services/pok_service.py:538-541` — the supersede path is exempt from `pok_family_full` (a same-slug supersede is count-preserving).

**Reading the numbers.** `prefix n` is a function of the family NAME only (it is a
`startswith` count), so it is identical in both views; the divergence lives in
`numbered n` (how many rows the view actually GATES) and in the family SET itself.
A family that is multi-token has `numbered n = 0` under CURRENT — it is not gated at
all today, so no admission ever consults its prefix count.

**CAUTION — near-collision under the OLD key.** `prompt-assent` and `prompt-assist` are DIFFERENT families under the corrected key, but under the OLD single-token key both collapse into the `prompt-` prefix — whose combined prefix count is **9 ACTIVE rows** (4 `prompt-assent-NN-*` + 2 unnumbered `prompt-assent-*` + 2 `prompt-assist-NN-*` + 1 unnumbered `prompt-assist-*`). Any merge plan that treats the `prompt-` prefix as ONE family is therefore hazardous: it merges two distinct families and over-counts by the other family's members.

## 1. Per-project totals (reconciliation)

Computed from the snapshot (ACTIVE rows enumerated) against the expected store
totals. Every cell below was asserted inside the generator; a mismatch aborts the
run before the artifact is written.

| project_id | active (computed) | active (expected) | archived | superseded | verdict |
|---|---|---|---|---|---|
| `83da04de-a410-4fb5-9e92-251a99d28a52` | 282 | 282 | 34 | 83 | MATCH |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | 217 | 217 | 8 | 25 | MATCH |
| `d5ef370a-d517-476e-9cbd-ce59b7229e8c` | 7 | 7 | 0 | 1 | MATCH |
| **TOTAL** | **506** | **506** | **42** | **109** | **MATCH** |

- Active total enumerated = **506** == snapshot `row_count` == `sha256_verified_count` == 506.
- Archived/superseded counts are taken from the snapshot's `project_status_counts` block and reconciled against the expected per-project values.
- Distinct ACTIVE rows carry no `status` field (the snapshot is already status-filtered to `active`); the archived/superseded legs are reconciled from the counts block, not re-derived from rows.
- **Zero active-count mismatch, zero delta.** The focus project `83da04de…` holds 282 ACTIVE rows.

## 2. Dual-view family tables

One table per project. `cur n` = rows the CURRENT key gates into the family;
`cur prefix` / `cor prefix` = ACTIVE rows whose slug starts with `<family>-`
(the `count_active_in_family` count, unnumbered rows included); `cor n` = rows the
CORRECTED key gates into the family; `delta` = `cor n - cur n`.
`multi` marks a family whose name contains a hyphen — such a family is UNGATED under
the current key.

### 2.1 project `83da04de-a410-4fb5-9e92-251a99d28a52`

ACTIVE rows: 282 | families under CORRECTED: 111 | families under CURRENT: 80

| family | cur n | cur prefix | cor n | cor prefix | delta (cor-cur) | multi |
|---|---|---|---|---|---|---|
| `prompt-assent` | 0 | 6 | 4 | 6 | +4 | yes |
| `skill` | 3 | 9 | 3 | 9 | +0 | no |
| `gc-p4` | 0 | 5 | 3 | 5 | +3 | yes |
| `skillab` | 3 | 5 | 3 | 5 | +0 | no |
| `worktree` | 3 | 5 | 3 | 5 | +0 | no |
| `gc-gate` | 0 | 4 | 3 | 4 | +3 | yes |
| `group-chat` | 0 | 4 | 3 | 4 | +3 | yes |
| `mutation` | 3 | 4 | 3 | 4 | +0 | no |
| `baseleg` | 3 | 3 | 3 | 3 | +0 | no |
| `bindfix` | 3 | 3 | 3 | 3 | +0 | no |
| `discord` | 3 | 3 | 3 | 3 | +0 | no |
| `disk` | 3 | 3 | 3 | 3 | +0 | no |
| `forensics` | 3 | 3 | 3 | 3 | +0 | no |
| `packconv` | 3 | 3 | 3 | 3 | +0 | no |
| `quarantine` | 3 | 3 | 3 | 3 | +0 | no |
| `seal` | 3 | 3 | 3 | 3 | +0 | no |
| `skill-e6` | 0 | 3 | 3 | 3 | +3 | yes |
| `snapshot-l3` | 0 | 3 | 3 | 3 | +3 | yes |
| `testgate` | 3 | 3 | 3 | 3 | +0 | no |
| `wiring` | 3 | 3 | 3 | 3 | +0 | no |
| `stage` | 2 | 7 | 2 | 7 | +0 | no |
| `git` | 2 | 4 | 2 | 4 | +0 | no |
| `sonic` | 2 | 4 | 2 | 4 | +0 | no |
| `verify` | 2 | 4 | 2 | 4 | +0 | no |
| `bind` | 2 | 3 | 2 | 3 | +0 | no |
| `launcher` | 2 | 3 | 2 | 3 | +0 | no |
| `promote` | 2 | 3 | 2 | 3 | +0 | no |
| `prompt-assist` | 0 | 3 | 2 | 3 | +2 | yes |
| `access-predicate` | 0 | 2 | 2 | 2 | +2 | yes |
| `anchor-ledger` | 0 | 2 | 2 | 2 | +2 | yes |
| `bootlog` | 2 | 2 | 2 | 2 | +0 | no |
| `fe-jest-gate` | 0 | 2 | 2 | 2 | +2 | yes |
| `flake` | 2 | 2 | 2 | 2 | +0 | no |
| `injection` | 2 | 2 | 2 | 2 | +0 | no |
| `land` | 2 | 2 | 2 | 2 | +0 | no |
| `packs-registration` | 0 | 2 | 2 | 2 | +2 | yes |
| `portfence` | 2 | 2 | 2 | 2 | +0 | no |
| `proxydec` | 2 | 2 | 2 | 2 | +0 | no |
| `release` | 2 | 2 | 2 | 2 | +0 | no |
| `review` | 2 | 2 | 2 | 2 | +0 | no |
| `sealmap` | 2 | 2 | 2 | 2 | +0 | no |
| `shutdown` | 2 | 2 | 2 | 2 | +0 | no |
| `skill-ab-resolve` | 0 | 2 | 2 | 2 | +2 | yes |
| `stagerelease` | 2 | 2 | 2 | 2 | +0 | no |
| `sysone` | 2 | 2 | 2 | 2 | +0 | no |
| `tmp` | 2 | 2 | 2 | 2 | +0 | no |
| `trustbound` | 2 | 2 | 2 | 2 | +0 | no |
| `wake-path` | 0 | 2 | 2 | 2 | +2 | yes |
| `wtgate` | 2 | 2 | 2 | 2 | +0 | no |
| `rogue` | 1 | 5 | 1 | 5 | +0 | no |
| `packs` | 1 | 4 | 1 | 4 | +0 | no |
| `canonical` | 1 | 2 | 1 | 2 | +0 | no |
| `census` | 1 | 2 | 1 | 2 | +0 | no |
| `digest` | 1 | 2 | 1 | 2 | +0 | no |
| `dispatch` | 1 | 2 | 1 | 2 | +0 | no |
| `evidence` | 1 | 2 | 1 | 2 | +0 | no |
| `source-gc` | 0 | 2 | 1 | 2 | +1 | yes |
| `abskill` | 1 | 1 | 1 | 1 | +0 | no |
| `agent` | 1 | 1 | 1 | 1 | +0 | no |
| `attest` | 1 | 1 | 1 | 1 | +0 | no |
| `bind-mut` | 0 | 1 | 1 | 1 | +1 | yes |
| `bindrace` | 1 | 1 | 1 | 1 | +0 | no |
| `board` | 1 | 1 | 1 | 1 | +0 | no |
| `bump` | 1 | 1 | 1 | 1 | +0 | no |
| `burstabort` | 1 | 1 | 1 | 1 | +0 | no |
| `counter` | 1 | 1 | 1 | 1 | +0 | no |
| `deathclock` | 1 | 1 | 1 | 1 | +0 | no |
| `decision-module` | 0 | 1 | 1 | 1 | +1 | yes |
| `decisionctl` | 1 | 1 | 1 | 1 | +0 | no |
| `dev-boot` | 0 | 1 | 1 | 1 | +1 | yes |
| `e2s0` | 1 | 1 | 1 | 1 | +0 | no |
| `fence` | 1 | 1 | 1 | 1 | +0 | no |
| `fidelity` | 1 | 1 | 1 | 1 | +0 | no |
| `gate-crosslane-pins` | 0 | 1 | 1 | 1 | +1 | yes |
| `gc-boot` | 0 | 1 | 1 | 1 | +1 | yes |
| `gcfe` | 1 | 1 | 1 | 1 | +0 | no |
| `guidelines` | 1 | 1 | 1 | 1 | +0 | no |
| `identity` | 1 | 1 | 1 | 1 | +0 | no |
| `killfoot` | 1 | 1 | 1 | 1 | +0 | no |
| `merge` | 1 | 1 | 1 | 1 | +0 | no |
| `packdrift` | 1 | 1 | 1 | 1 | +0 | no |
| `parallel` | 1 | 1 | 1 | 1 | +0 | no |
| `plan-predicate-ledger` | 0 | 1 | 1 | 1 | +1 | yes |
| `pok-scope` | 0 | 1 | 1 | 1 | +1 | yes |
| `pok-sweep` | 0 | 1 | 1 | 1 | +1 | yes |
| `predicate-discriminator` | 0 | 1 | 1 | 1 | +1 | yes |
| `proc` | 1 | 1 | 1 | 1 | +0 | no |
| `provider` | 1 | 1 | 1 | 1 | +0 | no |
| `pyinst` | 1 | 1 | 1 | 1 | +0 | no |
| `pytest` | 1 | 1 | 1 | 1 | +0 | no |
| `ram-guard` | 0 | 1 | 1 | 1 | +1 | yes |
| `retention` | 1 | 1 | 1 | 1 | +0 | no |
| `scheduling` | 1 | 1 | 1 | 1 | +0 | no |
| `scripts-pin` | 0 | 1 | 1 | 1 | +1 | yes |
| `sealcheck` | 1 | 1 | 1 | 1 | +0 | no |
| `shared-meta-kv` | 0 | 1 | 1 | 1 | +1 | yes |
| `skillab-manifest` | 0 | 1 | 1 | 1 | +1 | yes |
| `skillevo` | 1 | 1 | 1 | 1 | +0 | no |
| `sql` | 1 | 1 | 1 | 1 | +0 | no |
| `stageverify` | 1 | 1 | 1 | 1 | +0 | no |
| `staging` | 1 | 1 | 1 | 1 | +0 | no |
| `supervise` | 1 | 1 | 1 | 1 | +0 | no |
| `sweep` | 1 | 1 | 1 | 1 | +0 | no |
| `tag-read` | 0 | 1 | 1 | 1 | +1 | yes |
| `teigate` | 1 | 1 | 1 | 1 | +0 | no |
| `terminate-budget` | 0 | 1 | 1 | 1 | +1 | yes |
| `test-pack` | 0 | 1 | 1 | 1 | +1 | yes |
| `testenv` | 1 | 1 | 1 | 1 | +0 | no |
| `tick-budget` | 0 | 1 | 1 | 1 | +1 | yes |
| `uvvenv` | 1 | 1 | 1 | 1 | +0 | no |
| `wt` | 1 | 1 | 1 | 1 | +0 | no |

### 2.2 project `54debc61-f61c-4a59-bc02-2310e560a3a9`

ACTIVE rows: 217 | families under CORRECTED: 47 | families under CURRENT: 34

| family | cur n | cur prefix | cor n | cor prefix | delta (cor-cur) | multi |
|---|---|---|---|---|---|---|
| `model-copy` | 0 | 7 | 7 | 7 | +7 | yes |
| `fe-gate` | 0 | 8 | 6 | 8 | +6 | yes |
| `skill-evo` | 0 | 5 | 5 | 5 | +5 | yes |
| `binding-ttl` | 0 | 4 | 4 | 4 | +4 | yes |
| `deploy` | 3 | 4 | 3 | 4 | +0 | no |
| `worktree` | 3 | 4 | 3 | 4 | +0 | no |
| `buildgate` | 3 | 3 | 3 | 3 | +0 | no |
| `credgroups` | 3 | 3 | 3 | 3 | +0 | no |
| `credgrp` | 3 | 3 | 3 | 3 | +0 | no |
| `logs` | 3 | 3 | 3 | 3 | +0 | no |
| `pipeline` | 3 | 3 | 3 | 3 | +0 | no |
| `copy-model` | 0 | 5 | 2 | 5 | +2 | yes |
| `quota-mint-scope` | 0 | 5 | 2 | 5 | +2 | yes |
| `ship` | 2 | 5 | 2 | 5 | +0 | no |
| `verify` | 2 | 4 | 2 | 4 | +0 | no |
| `testpack` | 2 | 3 | 2 | 3 | +0 | no |
| `antivac` | 2 | 2 | 2 | 2 | +0 | no |
| `credentialrefvalid` | 2 | 2 | 2 | 2 | +0 | no |
| `dbsync` | 2 | 2 | 2 | 2 | +0 | no |
| `deltacheck` | 2 | 2 | 2 | 2 | +0 | no |
| `gateverdict` | 2 | 2 | 2 | 2 | +0 | no |
| `grouplane` | 2 | 2 | 2 | 2 | +0 | no |
| `landing` | 2 | 2 | 2 | 2 | +0 | no |
| `maketest` | 2 | 2 | 2 | 2 | +0 | no |
| `naming-sweep` | 0 | 2 | 2 | 2 | +2 | yes |
| `shipgate` | 2 | 2 | 2 | 2 | +0 | no |
| `credential-groups` | 0 | 8 | 1 | 8 | +1 | yes |
| `cachelayer` | 1 | 7 | 1 | 7 | +0 | no |
| `cred-groups` | 0 | 7 | 1 | 7 | +1 | yes |
| `fe-bundle` | 0 | 2 | 1 | 2 | +1 | yes |
| `abtest` | 1 | 1 | 1 | 1 | +0 | no |
| `copy-dialog` | 0 | 1 | 1 | 1 | +1 | yes |
| `credcooldown` | 1 | 1 | 1 | 1 | +0 | no |
| `deploylane` | 1 | 1 | 1 | 1 | +0 | no |
| `fe-scratch` | 0 | 1 | 1 | 1 | +1 | yes |
| `feship` | 1 | 1 | 1 | 1 | +0 | no |
| `festatic` | 1 | 1 | 1 | 1 | +0 | no |
| `frontend` | 1 | 1 | 1 | 1 | +0 | no |
| `gate` | 1 | 1 | 1 | 1 | +0 | no |
| `gofmt` | 1 | 1 | 1 | 1 | +0 | no |
| `imgembed` | 1 | 1 | 1 | 1 | +0 | no |
| `model-catalogs` | 0 | 1 | 1 | 1 | +1 | yes |
| `namingdelta` | 1 | 1 | 1 | 1 | +0 | no |
| `racegate` | 1 | 1 | 1 | 1 | +0 | no |
| `racepack` | 1 | 1 | 1 | 1 | +0 | no |
| `rollback` | 1 | 1 | 1 | 1 | +0 | no |
| `skillfix` | 1 | 1 | 1 | 1 | +0 | no |

### 2.3 project `d5ef370a-d517-476e-9cbd-ce59b7229e8c`

ACTIVE rows: 7 | families under CORRECTED: 4 | families under CURRENT: 4

| family | cur n | cur prefix | cor n | cor prefix | delta (cor-cur) | multi |
|---|---|---|---|---|---|---|
| `verify` | 3 | 3 | 3 | 3 | +0 | no |
| `deploy` | 2 | 2 | 2 | 2 | +0 | no |
| `catalog` | 1 | 1 | 1 | 1 | +0 | no |
| `environment` | 1 | 1 | 1 | 1 | +0 | no |

## 3. Blast radius vs `POK_FAMILY_MAX = 3` (CORRECTED view, all 3 projects)

### 3a. Families at or over the bound (corrected numbered count >= 3)

Exact member ids + slugs, machine-copied. `marker` = AT (== 3) or OVER (> 3).
`prefix n` is the `count_active_in_family` count that the live gate would use.

| project | family | corrected n | marker | prefix n | member id | member slug |
|---|---|---|---|---|---|---|
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `model-copy` | 7 | OVER | 7 | `05653c3b-de4b-42fc-bc40-5706934b0589` | `model-copy-03-atomicity` |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `model-copy` | 7 | OVER | 7 | `16a11904-4a33-4f06-b1f8-c69740113d93` | `model-copy-03-direct-id-resolution` |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `model-copy` | 7 | OVER | 7 | `7f6563d7-724f-450e-a9bf-12f4454a6f73` | `model-copy-02-routing` |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `model-copy` | 7 | OVER | 7 | `c5a81e88-9758-489a-8d4a-3b57d4bda766` | `model-copy-05-copy-dialog-reentrancy` |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `model-copy` | 7 | OVER | 7 | `d34d254c-8b16-45fe-b9b2-b663287cb732` | `model-copy-01-semantics` |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `model-copy` | 7 | OVER | 7 | `e85d8876-33ab-479e-ae13-e167b9ec1be5` | `model-copy-04-copy-dialog-close` |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `model-copy` | 7 | OVER | 7 | `eabfeb3e-d747-41c1-acac-2a407a8532e2` | `model-copy-02-route-mux` |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `fe-gate` | 6 | OVER | 8 | `2961d9f6-ffc5-4eee-8f65-bae50f6d8e27` | `fe-gate-03-vitest-econnrefused-noise` |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `fe-gate` | 6 | OVER | 8 | `2ef21e76-6d87-4a24-87f3-96adf7c5d0c1` | `fe-gate-04-vitest-run-cwd` |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `fe-gate` | 6 | OVER | 8 | `37ce9c2a-ee17-418e-b426-2fc881575e38` | `fe-gate-05-token-census-grep` |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `fe-gate` | 6 | OVER | 8 | `931ce826-7264-4cbf-8ca3-cf3d23e1684e` | `fe-gate-06-src-only-commit-not-failure` |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `fe-gate` | 6 | OVER | 8 | `bb073724-acb1-4bbd-a3b6-6504c67708bd` | `fe-gate-02-vite-zero-mod-proof` |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `fe-gate` | 6 | OVER | 8 | `c94f080a-6322-46de-baf7-3cde7c51b0ea` | `fe-gate-07-e8-token-exists-48-not-47` |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `skill-evo` | 5 | OVER | 5 | `3a77e49d-3d24-429d-8842-303c2309cdec` | `skill-evo-03-testpack-ab-deadlock` |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `skill-evo` | 5 | OVER | 5 | `45ba3e0d-ee6c-490a-8698-92265af8c8c1` | `skill-evo-02-testpack-addonly-patch-rule` |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `skill-evo` | 5 | OVER | 5 | `5a94310b-ce83-4177-b6c4-35225382a8d1` | `skill-evo-01-testpack-exitcode-gate` |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `skill-evo` | 5 | OVER | 5 | `688b9bcc-25a4-47b7-88bc-c6ac25f21d26` | `skill-evo-05-testpack-canonical-patch-spec` |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `skill-evo` | 5 | OVER | 5 | `ce3a2820-6eb1-4b44-b774-56ec9608295d` | `skill-evo-04-keeper-missing-deactivate-tool` |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `prompt-assent` | 4 | OVER | 6 | `03a6ad0a-3e07-43f2-914e-2b5c17a68d20` | `prompt-assent-03-paths-use-assent-not-assist` |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `prompt-assent` | 4 | OVER | 6 | `537b950c-79e0-44bd-960e-9e235a66d3e7` | `prompt-assent-04-dm-shield-tool-gate` |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `prompt-assent` | 4 | OVER | 6 | `6b58af57-7a84-4125-ac6d-e3bd55ea8c9e` | `prompt-assent-02-category-registration-seam` |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `prompt-assent` | 4 | OVER | 6 | `f91f7c0b-0fef-4a8b-8cc0-e2a117731c5d` | `prompt-assent-01-gate-owner-and-evidence` |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `binding-ttl` | 4 | OVER | 4 | `043bc856-1c09-4053-8cea-23f8b0791cc5` | `binding-ttl-03-grouplb-test-determinism` |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `binding-ttl` | 4 | OVER | 4 | `7b21cd68-48d2-4492-be58-95acfb71d187` | `binding-ttl-01-no-clamp-below-floor` |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `binding-ttl` | 4 | OVER | 4 | `a155efab-9839-4189-a814-0d4fd54f6800` | `binding-ttl-02-zero-ttl-normalized` |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `binding-ttl` | 4 | OVER | 4 | `c8128475-895e-4bcc-bfc6-bc48c47d097d` | `binding-ttl-03-config-dep-edge-preexisting` |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `skill` | 3 | AT | 9 | `0d8b1d7a-a743-4178-9bd3-dd982635a70e` | `skill-03-refusal-observability-split` |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `skill` | 3 | AT | 9 | `9c6975e7-f091-4584-b578-03e6571e6bb7` | `skill-01-off-by-absence-restart-asymmetry` |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `skill` | 3 | AT | 9 | `ae25adfe-c162-44af-8b9a-94b69a0dfcd6` | `skill-02-evolution-switch-precedence` |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `gc-p4` | 3 | AT | 5 | `8820fcd6-9851-45dc-8cd8-4885a55080db` | `gc-p4-02-executed-graph-probe-recipe` |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `gc-p4` | 3 | AT | 5 | `96c3c897-abb0-4e43-b6dd-f25c3fb5c847` | `gc-p4-03-note-drop-turn-end-reminder` |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `gc-p4` | 3 | AT | 5 | `ff138b47-ed4b-4fe8-aebb-b1f48ff72700` | `gc-p4-01-nudge-gate-executed-graph-green` |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `skillab` | 3 | AT | 5 | `07df8535-8389-4f53-9d50-86b683b73864` | `skillab-01-zero-traffic-ab-stall` |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `skillab` | 3 | AT | 5 | `63f64bbe-4f96-45a7-860e-bafb07aa7fca` | `skillab-02-keeper-rule-zero-traffic-fix` |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `skillab` | 3 | AT | 5 | `af8815b5-5623-439d-97d7-973bfe7a5c30` | `skillab-03-comparison-counter-semantics` |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `worktree` | 3 | AT | 5 | `88d8d1af-df59-4037-9e15-70b4a0570d47` | `worktree-03-upstream-auto-set-hazard` |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `worktree` | 3 | AT | 5 | `b29ef98f-af6c-426d-bdbe-d11f4e1d6e87` | `worktree-01-venv-branch-binding` |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `worktree` | 3 | AT | 5 | `f3fc3c4e-0b7e-4aad-9f88-9c03a913347f` | `worktree-02-python-version-divergence` |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `deploy` | 3 | AT | 4 | `c4f21a57-06b0-474e-b2de-12a14e8bf6e6` | `deploy-03-smoke-token-15m-daoduc` |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `deploy` | 3 | AT | 4 | `d19d5149-e2ec-423e-80d6-64670e62efc2` | `deploy-01-daoduc-backup` |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `deploy` | 3 | AT | 4 | `d7043ccb-bf9f-4796-aeea-aa65bd313804` | `deploy-02-ensem-dev-primary` |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `worktree` | 3 | AT | 4 | `49598f5e-38f2-43d9-b238-c5424b3792c8` | `worktree-03-gate-artifact-3way-merge` |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `worktree` | 3 | AT | 4 | `a696555d-22f0-4068-8ee4-8993d06d491d` | `worktree-01-env-fence` |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `worktree` | 3 | AT | 4 | `ff05895b-7d1f-43f2-962b-15237e4d758a` | `worktree-02-upstream-push-hazard` |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `gc-gate` | 3 | AT | 4 | `0a6870dc-77ba-4822-a86c-f03e7925acec` | `gc-gate-02-floor-scope-composition-flakes` |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `gc-gate` | 3 | AT | 4 | `5ddf201c-4879-478b-a759-5e6d909237f3` | `gc-gate-01-fence-family-exclusions` |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `gc-gate` | 3 | AT | 4 | `c1346dd6-5078-48cd-94a1-d16ee0c49a01` | `gc-gate-03-filter-side-pin` |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `group-chat` | 3 | AT | 4 | `68246f52-81a1-4cb6-8577-b6a1c18a973a` | `group-chat-03-p2-typed-collision-fix` |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `group-chat` | 3 | AT | 4 | `7e439bcf-1202-4084-855a-ea678e073e37` | `group-chat-01-design-landed` |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `group-chat` | 3 | AT | 4 | `e71966f5-75fa-49f6-ba95-01768d3afaba` | `group-chat-02-p1-committed-docs-stale` |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `mutation` | 3 | AT | 4 | `887dc716-4cd2-4366-b3c0-e2bc41a25e47` | `mutation-01-guards-need-counter-mutation` |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `mutation` | 3 | AT | 4 | `ab081457-5852-4aab-a27c-a750bbe6cb70` | `mutation-03-git-restore-byte-exact-proof` |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `mutation` | 3 | AT | 4 | `af8770ff-08b6-4f16-a861-d1f394248b7a` | `mutation-02-worktree-scratch-venue` |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `buildgate` | 3 | AT | 3 | `2cbf1391-ea89-424a-bbfc-2674815fabf5` | `buildgate-03-cachelayer-delta2` |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `buildgate` | 3 | AT | 3 | `2d56cd99-5c9c-4ad6-99cc-39a1fb07bfe0` | `buildgate-02-cachelayer-static-green` |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `buildgate` | 3 | AT | 3 | `8c8af3f1-4cdb-4ae7-8b4a-0b46568a408a` | `buildgate-01-ensure-critical-static-pack` |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `credgroups` | 3 | AT | 3 | `0543f20f-c0d9-470a-8792-d84b53acd4cc` | `credgroups-02-gofmt-zero-drift` |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `credgroups` | 3 | AT | 3 | `30069e28-8c1c-4316-8483-afabcebb9925` | `credgroups-01-packa-gate-verdict` |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `credgroups` | 3 | AT | 3 | `642db96c-bb95-42c7-83a4-44407f91c34a` | `credgroups-03-gofmt-nonvacuity-method` |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `credgrp` | 3 | AT | 3 | `97ae7590-4734-486a-a0db-ddbbc298eccc` | `credgrp-01-m1-dedup-keep-first` |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `credgrp` | 3 | AT | 3 | `a47f8aae-8d5f-48bb-a014-c8be6c43e797` | `credgrp-02-census-sql-only` |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `credgrp` | 3 | AT | 3 | `d848e541-782d-4961-b609-1acd31747d52` | `credgrp-03-repair-put-reindex` |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `logs` | 3 | AT | 3 | `6b0c55f6-86ff-4a19-b759-1ceb0045030f` | `logs-02-no-newlybound-means-one-credential` |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `logs` | 3 | AT | 3 | `88763f56-b051-49b5-abf2-f7fc10165249` | `logs-01-window-use-follow-to-file` |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `logs` | 3 | AT | 3 | `c3332273-2941-4970-ad32-ae16c382611c` | `logs-03-cred-id-is-member-id` |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `pipeline` | 3 | AT | 3 | `3e5f43a5-c8f3-43c6-bb0c-bef59c67d0ea` | `pipeline-01-merge-master-triggers-image-ci` |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `pipeline` | 3 | AT | 3 | `ae88da6f-feb1-4102-9504-494f719c5992` | `pipeline-03-deploy-order-daoduc-backup-first` |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `pipeline` | 3 | AT | 3 | `b78cec9e-e961-4dff-9d3d-f2077dfbbfbb` | `pipeline-02-verify-image-tag-digest-feature` |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `baseleg` | 3 | AT | 3 | `17782666-d708-4242-b6c3-3dad98590c20` | `baseleg-02-worktree-vs-archive-choice` |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `baseleg` | 3 | AT | 3 | `988c8902-f633-4f4a-af17-e1ef71c2b700` | `baseleg-01-git-archive-no-dotgit` |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `baseleg` | 3 | AT | 3 | `ec0c0cd6-6a4c-420d-bde3-e1e5dbe4b733` | `baseleg-01-phase1-base-red-set-protocol` |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `bindfix` | 3 | AT | 3 | `47a9e957-d487-4dd2-b2ac-41b50e4a02ca` | `bindfix-02-budget-for-transient-holders` |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `bindfix` | 3 | AT | 3 | `4a4ac838-3522-4774-8b21-7d6b053e9563` | `bindfix-03-blocked-boot-zero-startup` |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `bindfix` | 3 | AT | 3 | `ae5c5d2b-4cf9-4bba-81bb-53a7d5d15096` | `bindfix-01-prebind-without-listen` |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `discord` | 3 | AT | 3 | `33b8d86a-ddfc-4422-9d5a-7677eb9d795c` | `discord-01-channel-identity-already-present` |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `discord` | 3 | AT | 3 | `4ce6a0ec-150b-4da7-b29d-f5fc6104d6e6` | `discord-02-user-origin-arms-by-source-type` |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `discord` | 3 | AT | 3 | `ceef3982-96f2-403d-94b2-c67928136729` | `discord-03-assent-predicate-fix` |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `disk` | 3 | AT | 3 | `18d02e1d-4dc2-420d-b8c1-2268deba9676` | `disk-02-evidence-sidecar-manifest` |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `disk` | 3 | AT | 3 | `3446af40-1cce-4309-9f51-f359da7da6b8` | `disk-01-stage-wt-reclaim-gates` |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `disk` | 3 | AT | 3 | `bed25def-a69b-489a-9f62-9966b3208b24` | `disk-03-tag-lineage-unmerged-stage-trees` |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `forensics` | 3 | AT | 3 | `1ca5cd95-1b5f-41c4-878d-7ca3234d02b0` | `forensics-02-maintenance-runs-death-clock` |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `forensics` | 3 | AT | 3 | `796d5f18-d13e-4445-8623-3b0327cc30ec` | `forensics-02-debug-log-evicts-evidence` |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `forensics` | 3 | AT | 3 | `a5071152-2999-40cf-a49e-2f0a72366347` | `forensics-04-registry-truncates-command-text` |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `packconv` | 3 | AT | 3 | `0a5f7ac5-e382-42b0-9aca-daabbbeab9b9` | `packconv-01-extend-file-owes-no-new-row` |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `packconv` | 3 | AT | 3 | `4ec06afb-d0bf-464a-aeb1-ea3726900284` | `packconv-02-cite-packs-lines-at-base-tree` |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `packconv` | 3 | AT | 3 | `8075071c-f3a4-465b-b28b-5f35891c39b2` | `packconv-03-unregistered-pack-no-coverage` |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `quarantine` | 3 | AT | 3 | `235fcba0-a0e8-4a7a-b69b-6fbe36843fa1` | `quarantine-01-chart-seal-row-is-stale` |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `quarantine` | 3 | AT | 3 | `52ea474f-141a-41f2-84f0-91915bd1e2cb` | `quarantine-01-row-names-all-reds` |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `quarantine` | 3 | AT | 3 | `a17dd744-2be5-447d-a1ba-7941b2303e9c` | `quarantine-02-family-rows-need-node-mapping` |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `seal` | 3 | AT | 3 | `1e7126c5-2bce-40c5-a282-1688a02cf4e2` | `seal-01-restamp-requires-unseal-row` |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `seal` | 3 | AT | 3 | `5fcb2598-673e-4121-b5c0-c2e41d19b3ca` | `seal-02-repin-dict-keeps-base-value` |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `seal` | 3 | AT | 3 | `823d48a9-2c52-44e5-a7de-7c5d0695b66c` | `seal-03-repin-mutation-proof-triple` |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `skill-e6` | 3 | AT | 3 | `0df359fe-4a6b-4c93-9cae-816798a97ee5` | `skill-e6-04-a4-gauge-grew-not-collapsed` |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `skill-e6` | 3 | AT | 3 | `944483d7-0678-434c-a2c6-f6a0f09e003d` | `skill-e6-02-sweep-crowns-newer-gen` |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `skill-e6` | 3 | AT | 3 | `d5fde27d-8a2f-477e-b400-4faf1edb6216` | `skill-e6-03-promote-did-not-resolve-backlog` |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `snapshot-l3` | 3 | AT | 3 | `08580dbd-f343-4a1b-b329-c5f0d24f243b` | `snapshot-l3-01-malformed-json-empty-collapse` |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `snapshot-l3` | 3 | AT | 3 | `81447a12-7a29-463e-a0c2-514d9039c1fd` | `snapshot-l3-04-drift-pin-identity-hazard` |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `snapshot-l3` | 3 | AT | 3 | `d31fa038-69b7-4690-aa17-5a4817c007d6` | `snapshot-l3-02-base-red-set-one` |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `testgate` | 3 | AT | 3 | `28fbf220-25dc-48f2-877e-9a398a9eeeec` | `testgate-01-explicit-paths` |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `testgate` | 3 | AT | 3 | `31c5ad09-76e6-4c96-82a6-cb87cdb762ad` | `testgate-03-double-drift-hides-branch` |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `testgate` | 3 | AT | 3 | `c6a7a4ec-02c1-4802-bf3c-3488c8025b9b` | `testgate-02-standalone-packs` |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `wiring` | 3 | AT | 3 | `0e3dd745-ee9c-4c43-bc95-c49e1159fd28` | `wiring-01-capture-dispatcher-seam` |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `wiring` | 3 | AT | 3 | `c9dbbdc0-f3e7-4c3c-ab81-1e9ea9f878a4` | `wiring-03-grep-scoped-miss` |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `wiring` | 3 | AT | 3 | `ec808433-cd65-4076-95f5-7593f96e0906` | `wiring-02-phase2-window` |
| `d5ef370a-d517-476e-9cbd-ce59b7229e8c` | `verify` | 3 | AT | 3 | `0133757c-ff63-49e0-972c-681f04bce3ff` | `verify-03-jsonpath-null-trap` |
| `d5ef370a-d517-476e-9cbd-ce59b7229e8c` | `verify` | 3 | AT | 3 | `44490f84-b650-4639-89bf-631d26b6866b` | `verify-01-g8-maxtokens-512` |
| `d5ef370a-d517-476e-9cbd-ce59b7229e8c` | `verify` | 3 | AT | 3 | `782238c3-bca9-4240-86c4-58c8eea95efd` | `verify-02-registry-api-digest` |

- Families at or over the bound (corrected numbered n >= 3): **32** across 3 projects.
- Focus project `83da04de…`: **20**.

### 3b. NEW-refusal families (4th-member admissions that pass today and would refuse)

Definition applied: corrected numbered count >= 3 **AND** at least one corrected
member whose CURRENT-view family differs (which, for a multi-token family, means
*ungated today*). Today such a write is admitted (no family is parsed, so
`count_active_in_family` is never consulted); under the corrected key it hits
`pok_family_full`.

| project | family | corrected n | prefix n | would refuse at | member id | member slug | current family |
|---|---|---|---|---|---|---|---|
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `model-copy` | 7 | 7 | 8-th member | `05653c3b-de4b-42fc-bc40-5706934b0589` | `model-copy-03-atomicity` | None (ungated) |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `model-copy` | 7 | 7 | 8-th member | `16a11904-4a33-4f06-b1f8-c69740113d93` | `model-copy-03-direct-id-resolution` | None (ungated) |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `model-copy` | 7 | 7 | 8-th member | `7f6563d7-724f-450e-a9bf-12f4454a6f73` | `model-copy-02-routing` | None (ungated) |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `model-copy` | 7 | 7 | 8-th member | `c5a81e88-9758-489a-8d4a-3b57d4bda766` | `model-copy-05-copy-dialog-reentrancy` | None (ungated) |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `model-copy` | 7 | 7 | 8-th member | `d34d254c-8b16-45fe-b9b2-b663287cb732` | `model-copy-01-semantics` | None (ungated) |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `model-copy` | 7 | 7 | 8-th member | `e85d8876-33ab-479e-ae13-e167b9ec1be5` | `model-copy-04-copy-dialog-close` | None (ungated) |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `model-copy` | 7 | 7 | 8-th member | `eabfeb3e-d747-41c1-acac-2a407a8532e2` | `model-copy-02-route-mux` | None (ungated) |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `fe-gate` | 6 | 8 | 7-th member | `2961d9f6-ffc5-4eee-8f65-bae50f6d8e27` | `fe-gate-03-vitest-econnrefused-noise` | None (ungated) |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `fe-gate` | 6 | 8 | 7-th member | `2ef21e76-6d87-4a24-87f3-96adf7c5d0c1` | `fe-gate-04-vitest-run-cwd` | None (ungated) |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `fe-gate` | 6 | 8 | 7-th member | `37ce9c2a-ee17-418e-b426-2fc881575e38` | `fe-gate-05-token-census-grep` | None (ungated) |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `fe-gate` | 6 | 8 | 7-th member | `931ce826-7264-4cbf-8ca3-cf3d23e1684e` | `fe-gate-06-src-only-commit-not-failure` | None (ungated) |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `fe-gate` | 6 | 8 | 7-th member | `bb073724-acb1-4bbd-a3b6-6504c67708bd` | `fe-gate-02-vite-zero-mod-proof` | None (ungated) |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `fe-gate` | 6 | 8 | 7-th member | `c94f080a-6322-46de-baf7-3cde7c51b0ea` | `fe-gate-07-e8-token-exists-48-not-47` | None (ungated) |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `skill-evo` | 5 | 5 | 6-th member | `3a77e49d-3d24-429d-8842-303c2309cdec` | `skill-evo-03-testpack-ab-deadlock` | None (ungated) |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `skill-evo` | 5 | 5 | 6-th member | `45ba3e0d-ee6c-490a-8698-92265af8c8c1` | `skill-evo-02-testpack-addonly-patch-rule` | None (ungated) |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `skill-evo` | 5 | 5 | 6-th member | `5a94310b-ce83-4177-b6c4-35225382a8d1` | `skill-evo-01-testpack-exitcode-gate` | None (ungated) |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `skill-evo` | 5 | 5 | 6-th member | `688b9bcc-25a4-47b7-88bc-c6ac25f21d26` | `skill-evo-05-testpack-canonical-patch-spec` | None (ungated) |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `skill-evo` | 5 | 5 | 6-th member | `ce3a2820-6eb1-4b44-b774-56ec9608295d` | `skill-evo-04-keeper-missing-deactivate-tool` | None (ungated) |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `prompt-assent` | 4 | 6 | 5-th member | `03a6ad0a-3e07-43f2-914e-2b5c17a68d20` | `prompt-assent-03-paths-use-assent-not-assist` | None (ungated) |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `prompt-assent` | 4 | 6 | 5-th member | `537b950c-79e0-44bd-960e-9e235a66d3e7` | `prompt-assent-04-dm-shield-tool-gate` | None (ungated) |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `prompt-assent` | 4 | 6 | 5-th member | `6b58af57-7a84-4125-ac6d-e3bd55ea8c9e` | `prompt-assent-02-category-registration-seam` | None (ungated) |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `prompt-assent` | 4 | 6 | 5-th member | `f91f7c0b-0fef-4a8b-8cc0-e2a117731c5d` | `prompt-assent-01-gate-owner-and-evidence` | None (ungated) |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `binding-ttl` | 4 | 4 | 5-th member | `043bc856-1c09-4053-8cea-23f8b0791cc5` | `binding-ttl-03-grouplb-test-determinism` | None (ungated) |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `binding-ttl` | 4 | 4 | 5-th member | `7b21cd68-48d2-4492-be58-95acfb71d187` | `binding-ttl-01-no-clamp-below-floor` | None (ungated) |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `binding-ttl` | 4 | 4 | 5-th member | `a155efab-9839-4189-a814-0d4fd54f6800` | `binding-ttl-02-zero-ttl-normalized` | None (ungated) |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `binding-ttl` | 4 | 4 | 5-th member | `c8128475-895e-4bcc-bfc6-bc48c47d097d` | `binding-ttl-03-config-dep-edge-preexisting` | None (ungated) |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `gc-p4` | 3 | 5 | 4-th member | `8820fcd6-9851-45dc-8cd8-4885a55080db` | `gc-p4-02-executed-graph-probe-recipe` | None (ungated) |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `gc-p4` | 3 | 5 | 4-th member | `96c3c897-abb0-4e43-b6dd-f25c3fb5c847` | `gc-p4-03-note-drop-turn-end-reminder` | None (ungated) |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `gc-p4` | 3 | 5 | 4-th member | `ff138b47-ed4b-4fe8-aebb-b1f48ff72700` | `gc-p4-01-nudge-gate-executed-graph-green` | None (ungated) |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `gc-gate` | 3 | 4 | 4-th member | `0a6870dc-77ba-4822-a86c-f03e7925acec` | `gc-gate-02-floor-scope-composition-flakes` | None (ungated) |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `gc-gate` | 3 | 4 | 4-th member | `5ddf201c-4879-478b-a759-5e6d909237f3` | `gc-gate-01-fence-family-exclusions` | None (ungated) |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `gc-gate` | 3 | 4 | 4-th member | `c1346dd6-5078-48cd-94a1-d16ee0c49a01` | `gc-gate-03-filter-side-pin` | None (ungated) |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `group-chat` | 3 | 4 | 4-th member | `68246f52-81a1-4cb6-8577-b6a1c18a973a` | `group-chat-03-p2-typed-collision-fix` | None (ungated) |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `group-chat` | 3 | 4 | 4-th member | `7e439bcf-1202-4084-855a-ea678e073e37` | `group-chat-01-design-landed` | None (ungated) |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `group-chat` | 3 | 4 | 4-th member | `e71966f5-75fa-49f6-ba95-01768d3afaba` | `group-chat-02-p1-committed-docs-stale` | None (ungated) |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `skill-e6` | 3 | 3 | 4-th member | `0df359fe-4a6b-4c93-9cae-816798a97ee5` | `skill-e6-04-a4-gauge-grew-not-collapsed` | None (ungated) |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `skill-e6` | 3 | 3 | 4-th member | `944483d7-0678-434c-a2c6-f6a0f09e003d` | `skill-e6-02-sweep-crowns-newer-gen` | None (ungated) |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `skill-e6` | 3 | 3 | 4-th member | `d5fde27d-8a2f-477e-b400-4faf1edb6216` | `skill-e6-03-promote-did-not-resolve-backlog` | None (ungated) |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `snapshot-l3` | 3 | 3 | 4-th member | `08580dbd-f343-4a1b-b329-c5f0d24f243b` | `snapshot-l3-01-malformed-json-empty-collapse` | None (ungated) |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `snapshot-l3` | 3 | 3 | 4-th member | `81447a12-7a29-463e-a0c2-514d9039c1fd` | `snapshot-l3-04-drift-pin-identity-hazard` | None (ungated) |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `snapshot-l3` | 3 | 3 | 4-th member | `d31fa038-69b7-4690-aa17-5a4817c007d6` | `snapshot-l3-02-base-red-set-one` | None (ungated) |

- **New-refusal families: 10.**
  - `binding-ttl` in `54debc61-f61c-4a59-bc02-2310e560a3a9` — corrected numbered n=4, prefix n=4, multi-token=yes, currently ungated under the live key.
  - `fe-gate` in `54debc61-f61c-4a59-bc02-2310e560a3a9` — corrected numbered n=6, prefix n=8, multi-token=yes, currently ungated under the live key.
  - `model-copy` in `54debc61-f61c-4a59-bc02-2310e560a3a9` — corrected numbered n=7, prefix n=7, multi-token=yes, currently ungated under the live key.
  - `skill-evo` in `54debc61-f61c-4a59-bc02-2310e560a3a9` — corrected numbered n=5, prefix n=5, multi-token=yes, currently ungated under the live key.
  - `gc-gate` in `83da04de-a410-4fb5-9e92-251a99d28a52` — corrected numbered n=3, prefix n=4, multi-token=yes, currently ungated under the live key.
  - `gc-p4` in `83da04de-a410-4fb5-9e92-251a99d28a52` — corrected numbered n=3, prefix n=5, multi-token=yes, currently ungated under the live key.
  - `group-chat` in `83da04de-a410-4fb5-9e92-251a99d28a52` — corrected numbered n=3, prefix n=4, multi-token=yes, currently ungated under the live key.
  - `prompt-assent` in `83da04de-a410-4fb5-9e92-251a99d28a52` — corrected numbered n=4, prefix n=6, multi-token=yes, currently ungated under the live key.
  - `skill-e6` in `83da04de-a410-4fb5-9e92-251a99d28a52` — corrected numbered n=3, prefix n=3, multi-token=yes, currently ungated under the live key.
  - `snapshot-l3` in `83da04de-a410-4fb5-9e92-251a99d28a52` — corrected numbered n=3, prefix n=3, multi-token=yes, currently ungated under the live key.

### 3c. Unchanged gating — single-token families + PM headroom cross-check

**Asserted:** for every single-token family (name contains no hyphen) in every
project, `corrected numbered n == current numbered n`. The corrected key is a strict
SUPERSET of the current one, so a single-token family is gated identically and its
admission behaviour is unchanged.

- Single-token families checked: **118** across 3 projects.
- Assertion failures: **0** (the generator aborts before writing on any mismatch).
- Multi-token families (gated under CORRECTED, ungated under CURRENT): **44** across 3 projects; of those, **10** are at or over the corrected bound (see §3a) and **10** are a new-refusal family (see §3b).

**PM dual-key headroom table cross-check** (focus project `83da04de…`; PM table is
post-wave, ~18:36Z; this census is 2026-11-18T20:57:11Z):

| family | PM numbered | PM prefix | census numbered | census prefix | delta numbered | delta prefix |
|---|---|---|---|---|---|---|
| `prompt-assent` | 4 | 5 | 4 | 6 | +0 | +1 |
| `snapshot-l3` | 3 | 3 | 3 | 3 | +0 | +0 |
| `git` | 2 | 4 | 2 | 4 | +0 | +0 |

**Row-level attribution** for the three tracked families (member rows of the census):

- **`prompt-assent`** — PM 4/5 vs census 4/6 (delta +0 numbered, +1 prefix)
  - `03a6ad0a-3e07-43f2-914e-2b5c17a68d20` `prompt-assent-03-paths-use-assent-not-assist` — created 2026-11-11T04:04:36.698301+00:00 (by worker), updated 2026-11-11T04:04:36.698351+00:00
  - `537b950c-79e0-44bd-960e-9e235a66d3e7` `prompt-assent-04-dm-shield-tool-gate` — created 2026-11-12T04:04:22.362856+00:00 (by tester), updated 2026-11-12T04:04:22.362975+00:00
  - `6b58af57-7a84-4125-ac6d-e3bd55ea8c9e` `prompt-assent-02-category-registration-seam` — created 2026-11-11T00:36:32.758752+00:00 (by coder), updated 2026-11-11T00:36:32.758805+00:00
  - `f91f7c0b-0fef-4a8b-8cc0-e2a117731c5d` `prompt-assent-01-gate-owner-and-evidence` — created 2026-11-11T00:36:48.586765+00:00 (by coder), updated 2026-11-11T00:36:48.586813+00:00
  - (unnumbered prefix member) `6db7e0fd-a716-4463-8e5f-bfea1a118c87` `prompt-assent-flip-on-gate` — created 2026-11-18T15:06:50.439055+00:00, updated 2026-11-18T15:06:50.439118+00:00
  - (unnumbered prefix member) `a527d168-c0c3-4af0-90be-24b65895b222` `prompt-assent-pg-server-defaults-gap` — created 2026-11-11T07:44:55.982712+00:00, updated 2026-11-11T07:44:55.982771+00:00
- **`snapshot-l3`** — PM 3/3 vs census 3/3 (delta +0 numbered, +0 prefix)
  - `08580dbd-f343-4a1b-b329-c5f0d24f243b` `snapshot-l3-01-malformed-json-empty-collapse` — created 2026-11-18T18:34:30.841448+00:00 (by project-manager), updated 2026-11-18T18:34:30.841527+00:00
  - `81447a12-7a29-463e-a0c2-514d9039c1fd` `snapshot-l3-04-drift-pin-identity-hazard` — created 2026-11-09T11:12:58.815489+00:00 (by tester), updated 2026-11-09T11:12:58.815544+00:00
  - `d31fa038-69b7-4690-aa17-5a4817c007d6` `snapshot-l3-02-base-red-set-one` — created 2026-11-09T11:50:43.010610+00:00 (by kb-writer), updated 2026-11-09T11:50:43.010685+00:00
- **`git`** — PM 2/4 vs census 2/4 (delta +0 numbered, +0 prefix)
  - `c6749e2b-6141-424a-b993-de7877c0716d` `git-01-canonical-detached-diff-trap` — created 2026-11-18T17:34:32.074143+00:00 (by giter), updated 2026-11-18T17:34:32.074202+00:00
  - `fb0cc19f-b1ce-4fa9-b9e9-a91e7a9b38ec` `git-01-commit-msg-before-dashdash` — created 2026-11-11T07:54:56.842020+00:00 (by developer), updated 2026-11-11T07:54:56.842131+00:00
  - (unnumbered prefix member) `48b2c876-f0b6-46bc-8ec2-d0f576c95830` `git-sha-transcription-discipline` — created 2026-11-11T08:28:17.367059+00:00, updated 2026-11-11T08:28:17.367116+00:00
  - (unnumbered prefix member) `b02c4f39-5a05-4bc1-a2c2-58262d56e913` `git-update-ref-worktree-index-stale` — created 2026-11-18T17:34:32.068841+00:00, updated 2026-11-18T17:34:32.068972+00:00

**Ledger-vs-census diff** — the PM's stated composition (transcribed from
`family-cap-consolidation-2026-11-18.md`) diffed against the census. This is where
a delta is attributed to either (i) store drift in the window or (ii) a precision gap
in the PM ledger itself.

- **`prompt-assent`**
  - in census prefix set but NOT in the PM-stated set: `prompt-assent-pg-server-defaults-gap` (`a527d168-c0c3-4af0-90be-24b65895b222`) — created 2026-11-11T07:44:55.982712+00:00, updated 2026-11-11T07:44:55.982771+00:00 — PRE-WINDOW row, untouched since — PM-ledger undercount, NOT drift
- **`snapshot-l3`**
  - the PM doc does not enumerate the full slug set for this family (it names `snapshot-l3-01-malformed-json-empty-collapse` as the post-wave survivor and `snapshot-l3-05-decline-byte-identical` as the ARCHIVED absorbed member), so a slug-set diff is not possible for it; the counts are the comparable quantity — PM 3/3 vs census 3/3 — they MATCH.
- **`git`**
  - prefix slug sets are IDENTICAL to the PM-stated set — no composition drift.
  - id cross-check (PM-cited id prefix vs live ACTIVE id):
    - `git-01-canonical-detached-diff-trap` — PM id `cb7dea97` vs live ACTIVE id `c6749e2b-6141-424a-b993-de7877c0716d` (**MISMATCH**; the live row was created 2026-11-18T17:34:32.074143+00:00 by giter, i.e. BEFORE the PM wave, so the PM-cited id is not the live active id for this slug)
    - `git-01-commit-msg-before-dashdash` — PM id `fb0cc19f` == live `fb0cc19f-b1ce-4fa9-b9e9-a91e7a9b38ec` (MATCH)
    - `git-sha-transcription-discipline` — PM id `48b2c876` == live `48b2c876-f0b6-46bc-8ec2-d0f576c95830` (MATCH)
    - `git-update-ref-worktree-index-stale` — PM id `b02c4f39` == live `b02c4f39-5a05-4bc1-a2c2-58262d56e913` (MATCH)

**Window census** — ACTIVE rows in the focus project created after the PM wave
boundary 2026-11-18T18:36:00 (attribution for any drift above):

| id | slug | created_at | created_by_agent | corrected family | current family |
|---|---|---|---|---|---|
| `7a16a579-363f-4ea5-ba8f-a75d18b1475d` | `stage-recipe-fe-prebuild-required` | 2026-11-18T18:39:27.716835+00:00 | giter | None | None |
| `612fd972-e205-4de3-95bf-0b0704f35973` | `wake-path-02-ttl-backstop-not-firing` | 2026-11-18T18:40:26.345316+00:00 | kb-writer | wake-path | None |
| `9d85fcb7-51f0-4f58-b5c0-5df7e1be9976` | `wake-path-01-emitter-accept-skip` | 2026-11-18T18:40:26.428484+00:00 | kb-writer | wake-path | None |
| `91faa761-088c-4970-8e38-f0fb53317020` | `scripts-pin-01-floor-087f782af` | 2026-11-18T19:06:05.255121+00:00 | worker | scripts-pin | None |
| `00a16427-3746-45c9-ad82-872a58222f45` | `promote-scripts-dir-provenance` | 2026-11-18T19:06:10.390821+00:00 | worker | None | None |
| `8870e04f-f65a-4956-bf18-11f4a32825bc` | `retention-01-evict-sort-key` | 2026-11-18T19:06:18.601566+00:00 | worker | retention | retention |
| `a3ef32a0-c48b-481f-86a9-8eb1b5b05f18` | `verify-script-vacuous-pass-dash-shell-guard` | 2026-11-18T19:07:18.542634+00:00 | worker | None | None |
| `a0a5384d-d97f-44d0-aa9d-66669c4743d7` | `attest-01-drift-gate-proceed-on-base` | 2026-11-18T20:02:33.928500+00:00 | tester | attest | attest |

### 3d. Unnumbered-prefix rows counted toward `prefix_count` but not gated themselves

Rows whose slug starts with `<family>-` (so the family prefix count includes them)
but which are NOT numbered members of that family. Two shapes are separated below,
because they mean different things to the gate:

- **3d.1 — family extraction `None`:** the row is not gated by ANY key. It still
  counts toward the family's prefix count, so it adds pressure to the bound without
  being a member the PM could merge, supersede or archive as part of the family.
- **3d.2 — family extraction is a LONGER family `G` (`G` starts with `<family>-`):**
  the row IS gated, but under `G`, not under the shorter family. It inflates the
  shorter family's prefix count while belonging to a different family — a prefix
  collision, not a loose member.

#### 3d.1 family extraction `None` (ungated rows inside a family prefix)

| project | family | prefix n | row id | row slug | family extraction |
|---|---|---|---|---|---|
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `cachelayer` | 7 | `08936131-b714-40da-8903-d7abfc6242ca` | `cachelayer-review-delta-approve` | None |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `cachelayer` | 7 | `5c08c944-2842-4cb2-9da4-9a08a24fd3bf` | `cachelayer-import-pin-real-name` | None |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `cachelayer` | 7 | `8ea090f0-bd75-4c6e-bdde-6914508326fe` | `cachelayer-packb-gate2-approve` | None |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `cachelayer` | 7 | `a4cd471b-ad98-460f-8e75-7c765a4e89f1` | `cachelayer-pin-onehop-reach-gap` | None |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `cachelayer` | 7 | `ab232a5a-c49f-4ad7-84e3-39b2a33d31f1` | `cachelayer-review-approve` | None |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `cachelayer` | 7 | `d686b166-9ba1-4270-bf92-e8f3a04fb847` | `cachelayer-event-vocab-model-failed` | None |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `copy-model` | 5 | `3828721a-df0b-4adf-8d7c-8594037e79ef` | `copy-model-gate-verdict-2026-11-09` | None |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `copy-model` | 5 | `51cdcc74-ae6a-4c31-913f-508546d2f773` | `copy-model-semantics` | None |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `copy-model` | 5 | `c013a3ca-f0c3-4077-a1c2-988297c9cef1` | `copy-model-landed-master-5ce0ead` | None |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `cred-groups` | 7 | `1865da53-c9af-4246-b54c-02ff9b3cb111` | `cred-groups-plan-anchor-corrections` | None |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `cred-groups` | 7 | `245e8717-21ee-49d4-bd5e-bbc0553e1ada` | `cred-groups-flatten-accessor-shape` | None |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `cred-groups` | 7 | `35fe89b8-f605-40ad-a486-947478847d4d` | `cred-groups-reconcile-write-lock` | None |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `cred-groups` | 7 | `4870afa3-c894-4819-8cd5-7aff3beb7c46` | `cred-groups-a2-flatview-never-empty` | None |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `cred-groups` | 7 | `a2087655-dbf3-488e-8ecb-f846b15c7421` | `cred-groups-amendment-half-application` | None |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `cred-groups` | 7 | `e900ce78-c54c-438f-8a4a-21116b4c56e7` | `cred-groups-fe-baseline-overlap` | None |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `credential-groups` | 8 | `5bdf7ea3-9e53-4b65-b01a-2081b5ff97c6` | `credential-groups-flatten-authority` | None |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `credential-groups` | 8 | `6719266d-63ed-43dc-8c05-9463ce91707c` | `credential-groups-flat-seam-interface` | None |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `credential-groups` | 8 | `a12bba3c-7a6b-44a1-87a3-e4ccb545f694` | `credential-groups-usedby-batcher-optional` | None |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `credential-groups` | 8 | `bb7a9299-41f1-4504-b054-7e780ba981fb` | `credential-groups-flatten-seam` | None |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `credential-groups` | 8 | `c4d4c5eb-db36-4c47-8954-cf00d4d6986a` | `credential-groups-groupsmu-lock-order` | None |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `credential-groups` | 8 | `d8d5b406-4955-4c8f-8fe4-84e5b5f224f9` | `credential-groups-reference-set` | None |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `credential-groups` | 8 | `e1dbb8a9-1f64-4659-8d6c-74114ce75f36` | `credential-groups-usedby-409-same-set` | None |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `deploy` | 4 | `318928d1-86fc-491f-b0e9-e3f4ea8e96fa` | `deploy-authorization-auto-deploy` | None |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `fe-bundle` | 2 | `f89b30c4-1380-4b78-8ace-3b057652bb3b` | `fe-bundle-byte-identity-type-only-edits` | None |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `fe-gate` | 8 | `6bb2d1e3-4787-4d9c-bb06-7300267984c7` | `fe-gate-tsc-baseline-credgroups-18of9` | None |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `fe-gate` | 8 | `9e3080a8-1c16-4a3a-8a4c-58116d3e82d9` | `fe-gate-tsc-baseline-parity` | None |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `quota-mint-scope` | 5 | `9d79f963-6f9b-47c0-9fcc-41062defc9cb` | `quota-mint-scope-account-walls` | None |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `quota-mint-scope` | 5 | `c86ee701-2616-4968-a9d0-7b3b1e01af9f` | `quota-mint-scope-f8-exposure-a2-budget` | None |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `quota-mint-scope` | 5 | `c8b411bf-f0a6-4fbf-99fd-2511e7bdd18d` | `quota-mint-scope-merge-surface` | None |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `ship` | 5 | `47641ccd-7beb-4d55-95d9-6078a3d6c8c1` | `ship-v202-0082773-both-hosts` | None |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `ship` | 5 | `cbccf948-7f1e-427e-a4b9-7fa4cc4f3ba1` | `ship-v199-3891e9d-both-hosts` | None |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `ship` | 5 | `de8e802a-9ccd-4278-9d8a-1dc1aa65045b` | `ship-v198-fc4423e-both-hosts` | None |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `testpack` | 3 | `96a565f4-97a1-4244-96f7-987c4ea3f3a6` | `testpack-store-race-runtime` | None |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `verify` | 4 | `650bc28c-b6f7-40cf-bfa3-ddc5e89b65cf` | `verify-image-stale-config-blob` | None |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `verify` | 4 | `b609270b-6923-4201-a809-6432f3cdf63c` | `verify-image-stale-tag-cache` | None |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `worktree` | 4 | `cf91010a-ffa6-437e-bc57-d8ecafcc29c1` | `worktree-shared-refs-midtask-tip-drift` | None |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `canonical` | 2 | `5ee30592-7c56-468d-8bfc-97198a3224bc` | `canonical-checkout-clean-20261114` | None |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `census` | 2 | `a883b76b-77cd-45b0-b9d0-b0c9acd8bc24` | `census-pin-lane-ownership-form` | None |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `digest` | 2 | `f54bb522-0210-4a69-9b3d-0fb200addd9b` | `digest-transcription-discipline` | None |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `dispatch` | 2 | `84a12798-6be5-4628-96b3-92dfc20e90fa` | `dispatch-truncation-recover-queue` | None |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `evidence` | 2 | `28889a8b-1234-4f28-88c6-6bce224d12ec` | `evidence-commit-ab-fdc2bb4f-restored` | None |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `gc-gate` | 4 | `1cd099e4-3df3-4715-9cfd-f84a4df588cb` | `gc-gate-fence-family-and-xunit2-digest-trap` | None |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `gc-p4` | 5 | `a98aed66-f8f1-41c8-9b81-215cbfd261f1` | `gc-p4-cle-line-tripwire-repin` | None |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `gc-p4` | 5 | `ccf336d0-2ff7-4764-a062-8666083e0bfc` | `gc-p4-junitxml-is-the-red-source` | None |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `git` | 4 | `48b2c876-f0b6-46bc-8ec2-d0f576c95830` | `git-sha-transcription-discipline` | None |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `git` | 4 | `b02c4f39-5a05-4bc1-a2c2-58262d56e913` | `git-update-ref-worktree-index-stale` | None |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `group-chat` | 4 | `a6e24bfd-79f5-4d16-8b67-42d2a3fbe6b4` | `group-chat-p4-nudge-latch-manager-ram` | None |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `launcher` | 3 | `c06b531f-3310-4cf2-8d74-645ba6ce762e` | `launcher-stats-restart-forensics` | None |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `mutation` | 4 | `1e461c98-78d0-4674-ae43-b5c9e04d8086` | `mutation-proof-must-target-pin-property` | None |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `packs` | 4 | `e025f995-669c-4cb2-9a52-d502b4184bf5` | `packs-row-count-drift-after-style-pass` | None |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `promote` | 3 | `00a16427-3746-45c9-ad82-872a58222f45` | `promote-scripts-dir-provenance` | None |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `prompt-assent` | 6 | `6db7e0fd-a716-4463-8e5f-bfea1a118c87` | `prompt-assent-flip-on-gate` | None |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `prompt-assent` | 6 | `a527d168-c0c3-4af0-90be-24b65895b222` | `prompt-assent-pg-server-defaults-gap` | None |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `prompt-assist` | 3 | `b0808ff3-0289-418b-821f-d474b913f833` | `prompt-assist-tier2-gate-netnew-2` | None |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `rogue` | 5 | `78f7a7ca-01e8-471a-a903-0056bcf9ec97` | `rogue-cleanup-rider-recorded-addendum` | None |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `rogue` | 5 | `8c05b44a-10e0-44c6-a3ac-8e6958b3bd97` | `rogue-p2-evidence-locations` | None |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `rogue` | 5 | `931d44a9-cec2-40f7-a677-dada29acc51f` | `rogue-p2-corpus-routed-to-v03012-rulings` | None |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `rogue` | 5 | `e9e65ac8-50f8-47a8-b2ff-32d0056c07c9` | `rogue-fence-ambient-allow-var` | None |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `skill` | 9 | `83a4b2d3-dd41-4f85-b799-11db50aefaee` | `skill-lane-flip-verify-restart` | None |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `skillab` | 5 | `47bf5bc7-17ae-45e7-a0a1-4ca2f96d5f2b` | `skillab-dev-zero-rows-ro-grant-gap` | None |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `sonic` | 4 | `c5eab064-6b05-4697-b85a-9ccc2f6647c5` | `sonic-stage-fresh-wt-fe-prebuild` | None |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `sonic` | 4 | `df5f8463-318a-4373-8c79-5f5bc102cfba` | `sonic-bump-version-selfcommits` | None |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `source-gc` | 2 | `b93a2b9a-72b0-403d-8f9b-2d3f9b316fd4` | `source-gc-drift-guards-branch-tip-sensitive` | None |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `stage` | 7 | `7a16a579-363f-4ea5-ba8f-a75d18b1475d` | `stage-recipe-fe-prebuild-required` | None |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `stage` | 7 | `80cfdede-096d-4fe6-8be5-2526362cfaa0` | `stage-release-gate-sequence` | None |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `stage` | 7 | `9938e652-125c-46f8-8e8d-6ba11c1a0b02` | `stage-sh-live-requires-live-arming-var` | None |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `stage` | 7 | `aa71a679-1776-4c1f-80d0-6f52026e5792` | `stage-ceremony-census-proofs` | None |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `stage` | 7 | `f87d0ddb-2095-44e7-aac1-a6cf4866a8ce` | `stage-v0300-worktree-head-nuance` | None |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `verify` | 4 | `42483d0d-1842-4331-b18f-00448d6a4e7a` | `verify-plan-ruling-sync-grep-derived` | None |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `verify` | 4 | `a3ef32a0-c48b-481f-86a9-8eb1b5b05f18` | `verify-script-vacuous-pass-dash-shell-guard` | None |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `worktree` | 5 | `8c548d8f-1c52-4109-bc2c-a9543bc2c778` | `worktree-deletion-claim-verify-first` | None |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `worktree` | 5 | `f28a5c98-7326-44a2-95b3-110f0f288b7f` | `worktree-env-fence-shape-recheck` | None |

- Rows in this shape: **72**.

#### 3d.2 cross-family prefix rows (row belongs to a LONGER family `G`)

| project | family (shorter) | row id | row slug | extraction (G) | G numbered n | G prefix n |
|---|---|---|---|---|---|---|
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `bind` | `5a20bfca-0bc3-4a2b-8906-2a1ba3fddf4a` | `bind-mut-01-scratch-wt-py-resolve` | `bind-mut` | 1 | 1 |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `packs` | `77df7328-7d17-4680-a5b0-1710d323d3ad` | `packs-registration-01-canonical-stale` | `packs-registration` | 2 | 2 |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `packs` | `8719ee0d-0e3b-4010-a8c8-a0f9875751ef` | `packs-registration-02-house-grep-audit` | `packs-registration` | 2 | 2 |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `skill` | `0a2a4864-408f-43b4-81dd-109ac1458d21` | `skill-ab-resolve-01-guard-parity-all-paths` | `skill-ab-resolve` | 2 | 2 |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `skill` | `0df359fe-4a6b-4c93-9cae-816798a97ee5` | `skill-e6-04-a4-gauge-grew-not-collapsed` | `skill-e6` | 3 | 3 |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `skill` | `944483d7-0678-434c-a2c6-f6a0f09e003d` | `skill-e6-02-sweep-crowns-newer-gen` | `skill-e6` | 3 | 3 |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `skill` | `bf54c278-c9fd-4aa9-b216-728aebf0b6cb` | `skill-ab-resolve-02-svc-sanctioned-unpinned` | `skill-ab-resolve` | 2 | 2 |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `skill` | `d5fde27d-8a2f-477e-b400-4faf1edb6216` | `skill-e6-03-promote-did-not-resolve-backlog` | `skill-e6` | 3 | 3 |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `skillab` | `58051960-f3e5-493d-9a9e-fb7f94451052` | `skillab-manifest-01-no-consumer` | `skillab-manifest` | 1 | 1 |

- Rows in this shape: **9**.
- **Gate consequence:** for the affected short families the prefix count is larger
  than the family's own member set, so a short family can sit at/over the bound
  while owning fewer than 3 rows of its own. Both quantities are listed for every
  family in §2 and §3a.

## 3e. All newly-refusing families (complete)

**Recomputed from the RAW census JSON** (`/tmp/pok_census_raw_20261118.json`, sha256 `d8a6e80b…`, 506 ACTIVE rows across 3 projects) by a python3-stdlib script (no DB access, no network) — **the caller's partial list was NOT copied; every row below is machine-derived.**

**Definition.** A corrected-view family `F` is *newly-refusing* iff `prefix_count(F) >= POK_FAMILY_MAX (3)` AND `F` was **NOT gated under the OLD (single-token) key** — i.e. `F` is multi-token, so the old key parsed NO family for its rows (or its old-view family differs). `prefix_count(F)` = ACTIVE rows whose slug `startswith(F + "-")` (view-independent: a `startswith` count, never a family-membership test); `numbered n` = ACTIVE rows whose CORRECTED family is exactly `F`.

**Result: 15 families — 12 OVER the prefix cap + 3 AT the prefix cap.** §3b above lists 10 of them under a narrower definition (corrected *numbered* n >= 3 AND a member whose CURRENT-view family differs); §3e is the complete set under the PREFIX-COUNT definition the live gate actually uses.

| project | family | numbered n | prefix n | member ids | member slugs |
|---|---|---|---|---|---|
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `credential-groups` | 1 | 8 | `1a60ee8b-4845-4eb0-8d64-3ccac69ade20` | `credential-groups-01-e1-neg-cache-absent` |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `fe-gate` | 6 | 8 | `bb073724-acb1-4bbd-a3b6-6504c67708bd`; `2961d9f6-ffc5-4eee-8f65-bae50f6d8e27`; `2ef21e76-6d87-4a24-87f3-96adf7c5d0c1`; `37ce9c2a-ee17-418e-b426-2fc881575e38`; `931ce826-7264-4cbf-8ca3-cf3d23e1684e`; `c94f080a-6322-46de-baf7-3cde7c51b0ea` | `fe-gate-02-vite-zero-mod-proof`; `fe-gate-03-vitest-econnrefused-noise`; `fe-gate-04-vitest-run-cwd`; `fe-gate-05-token-census-grep`; `fe-gate-06-src-only-commit-not-failure`; `fe-gate-07-e8-token-exists-48-not-47` |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `cred-groups` | 1 | 7 | `d383c4d8-6966-4b19-80d2-6d4234227ade` | `cred-groups-04-kind-gate-chat-only` |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `model-copy` | 7 | 7 | `d34d254c-8b16-45fe-b9b2-b663287cb732`; `eabfeb3e-d747-41c1-acac-2a407a8532e2`; `7f6563d7-724f-450e-a9bf-12f4454a6f73`; `05653c3b-de4b-42fc-bc40-5706934b0589`; `16a11904-4a33-4f06-b1f8-c69740113d93`; `e85d8876-33ab-479e-ae13-e167b9ec1be5`; `c5a81e88-9758-489a-8d4a-3b57d4bda766` | `model-copy-01-semantics`; `model-copy-02-route-mux`; `model-copy-02-routing`; `model-copy-03-atomicity`; `model-copy-03-direct-id-resolution`; `model-copy-04-copy-dialog-close`; `model-copy-05-copy-dialog-reentrancy` |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `prompt-assent` | 4 | 6 | `f91f7c0b-0fef-4a8b-8cc0-e2a117731c5d`; `6b58af57-7a84-4125-ac6d-e3bd55ea8c9e`; `03a6ad0a-3e07-43f2-914e-2b5c17a68d20`; `537b950c-79e0-44bd-960e-9e235a66d3e7` | `prompt-assent-01-gate-owner-and-evidence`; `prompt-assent-02-category-registration-seam`; `prompt-assent-03-paths-use-assent-not-assist`; `prompt-assent-04-dm-shield-tool-gate` |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `copy-model` | 2 | 5 | `24709d86-2f97-4895-970a-373f4f31181a`; `ef3daedd-4442-4049-be8b-ce8c0e134e88` | `copy-model-01-marker-false-positive`; `copy-model-02-ship-image-v189` |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `gc-p4` | 3 | 5 | `ff138b47-ed4b-4fe8-aebb-b1f48ff72700`; `8820fcd6-9851-45dc-8cd8-4885a55080db`; `96c3c897-abb0-4e43-b6dd-f25c3fb5c847` | `gc-p4-01-nudge-gate-executed-graph-green`; `gc-p4-02-executed-graph-probe-recipe`; `gc-p4-03-note-drop-turn-end-reminder` |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `quota-mint-scope` | 2 | 5 | `963b52da-9115-4740-86f6-856efeca6daf`; `4f2e76c3-b87d-4ec7-83f9-1219f4aebf84` | `quota-mint-scope-01-model-layer-empty`; `quota-mint-scope-02-account-body-layers` |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `skill-evo` | 5 | 5 | `5a94310b-ce83-4177-b6c4-35225382a8d1`; `45ba3e0d-ee6c-490a-8698-92265af8c8c1`; `3a77e49d-3d24-429d-8842-303c2309cdec`; `ce3a2820-6eb1-4b44-b774-56ec9608295d`; `688b9bcc-25a4-47b7-88bc-c6ac25f21d26` | `skill-evo-01-testpack-exitcode-gate`; `skill-evo-02-testpack-addonly-patch-rule`; `skill-evo-03-testpack-ab-deadlock`; `skill-evo-04-keeper-missing-deactivate-tool`; `skill-evo-05-testpack-canonical-patch-spec` |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `binding-ttl` | 4 | 4 | `7b21cd68-48d2-4492-be58-95acfb71d187`; `a155efab-9839-4189-a814-0d4fd54f6800`; `c8128475-895e-4bcc-bfc6-bc48c47d097d`; `043bc856-1c09-4053-8cea-23f8b0791cc5` | `binding-ttl-01-no-clamp-below-floor`; `binding-ttl-02-zero-ttl-normalized`; `binding-ttl-03-config-dep-edge-preexisting`; `binding-ttl-03-grouplb-test-determinism` |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `gc-gate` | 3 | 4 | `5ddf201c-4879-478b-a759-5e6d909237f3`; `0a6870dc-77ba-4822-a86c-f03e7925acec`; `c1346dd6-5078-48cd-94a1-d16ee0c49a01` | `gc-gate-01-fence-family-exclusions`; `gc-gate-02-floor-scope-composition-flakes`; `gc-gate-03-filter-side-pin` |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `group-chat` | 3 | 4 | `7e439bcf-1202-4084-855a-ea678e073e37`; `e71966f5-75fa-49f6-ba95-01768d3afaba`; `68246f52-81a1-4cb6-8577-b6a1c18a973a` | `group-chat-01-design-landed`; `group-chat-02-p1-committed-docs-stale`; `group-chat-03-p2-typed-collision-fix` |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `prompt-assist` | 2 | 3 | `6cdf61f7-a340-4624-8db8-9342c6a377dc`; `170d495f-fb72-4100-a9c0-f23f641e08fa` | `prompt-assist-01-discord-first-mint`; `prompt-assist-04-freshness-leg-second-race` |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `skill-e6` | 3 | 3 | `944483d7-0678-434c-a2c6-f6a0f09e003d`; `d5fde27d-8a2f-477e-b400-4faf1edb6216`; `0df359fe-4a6b-4c93-9cae-816798a97ee5` | `skill-e6-02-sweep-crowns-newer-gen`; `skill-e6-03-promote-did-not-resolve-backlog`; `skill-e6-04-a4-gauge-grew-not-collapsed` |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `snapshot-l3` | 3 | 3 | `08580dbd-f343-4a1b-b329-c5f0d24f243b`; `d31fa038-69b7-4690-aa17-5a4817c007d6`; `81447a12-7a29-463e-a0c2-514d9039c1fd` | `snapshot-l3-01-malformed-json-empty-collapse`; `snapshot-l3-02-base-red-set-one`; `snapshot-l3-04-drift-pin-identity-hazard` |

**Reconciliation vs the caller's partial list (12 names).** credential-groups 1/8, fe-gate 6/8, cred-groups 1/7, model-copy 7/7, prompt-assent 4/6, skill-evo 5/5, copy-model 2/5, quota-mint-scope 2/5, gc-p4 3/5, binding-ttl 4/4, gc-gate 3/4, group-chat 3/4 — **all 12 reproduce EXACTLY** (same family, same numbered n, same prefix n): **no divergence in the over-cap set.** The caller's list did not name the 3 at-cap refusers; §3e adds exactly those three — `prompt-assist` 2/3 (named by the reviewer), `skill-e6` 3/3 and `snapshot-l3` 3/3. No family in the caller's list is absent from §3e.

### 3e.1 First-numbered-admission class

**Definition.** Families whose PREFIX count is already `>= POK_FAMILY_MAX` while their own NUMBERED count is `< 3` — the family has not filled its own ordinal slots, but the prefix superset already trips the gate, so the FIRST (or next) numbered admission into it is refused. Example: `gc-p3` — prefix count 3 (three unnumbered `gc-p3-*` rows), numbered n = 0 today → the first `gc-p3-01-*` write now refuses.

**Exact counts (machine-derived).**

- **n = 0** (no numbered member yet — the FIRST numbered admission refuses): **29 families**.
- **n = 1..2** (one or two numbered members — the NEXT numbered admission refuses): **18 families**.
- Total under the literal `numbered n < 3` wording: **47**.

The caller's "~29 expected" matches the **n = 0 sub-class exactly (29)**; the n = 1..2 sub-class (18) is the same effect one or two ordinals later. Both sub-classes are listed below.

**n = 0 — the FIRST numbered admission refuses (29 families):**

| project | family | numbered n | prefix n |
|---|---|---|---|
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `agenticfix` | 0 | 3 |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `binding` | 0 | 4 |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `copy` | 0 | 6 |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `cred` | 0 | 7 |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `credential` | 0 | 8 |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `decision` | 0 | 6 |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `decision-groups` | 0 | 3 |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `fe` | 0 | 21 |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `llmproxy` | 0 | 8 |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `model` | 0 | 8 |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `models` | 0 | 3 |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `modelscache` | 0 | 3 |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `quota` | 0 | 5 |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `quota-mint` | 0 | 5 |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `skill` | 0 | 6 |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `smoke` | 0 | 3 |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `decision` | 0 | 4 |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `fe` | 0 | 5 |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `fe-bundle` | 0 | 3 |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `gate` | 0 | 5 |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `gc` | 0 | 17 |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `gc-p3` | 0 | 3 |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `gc-p5` | 0 | 3 |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `group` | 0 | 4 |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `plan` | 0 | 4 |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `prompt` | 0 | 9 |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `proxy` | 0 | 3 |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `snapshot` | 0 | 3 |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `views` | 0 | 5 |

**n = 1..2 — the NEXT numbered admission refuses (18 families):**

| project | family | numbered n | prefix n |
|---|---|---|---|
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `cachelayer` | 1 | 7 |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `copy-model` | 2 | 5 |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `cred-groups` | 1 | 7 |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `credential-groups` | 1 | 8 |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `quota-mint-scope` | 2 | 5 |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `ship` | 2 | 5 |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `testpack` | 2 | 3 |
| `54debc61-f61c-4a59-bc02-2310e560a3a9` | `verify` | 2 | 4 |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `bind` | 2 | 3 |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `git` | 2 | 4 |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `launcher` | 2 | 3 |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `packs` | 1 | 4 |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `promote` | 2 | 3 |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `prompt-assist` | 2 | 3 |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `rogue` | 1 | 5 |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `sonic` | 2 | 4 |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `stage` | 2 | 7 |
| `83da04de-a410-4fb5-9e92-251a99d28a52` | `verify` | 2 | 4 |

## 4. Next consolidation wave starting map

This section is the PM's next-wave map: every family that is AT or OVER the bound
under the corrected key, with the exact member ids + slugs (numbered members and
unnumbered-prefix members separated).

**Priority A — OVER the bound (corrected numbered n > 3): 5 families**

- `model-copy` (project `54debc61-f61c-4a59-bc02-2310e560a3a9`) — corrected numbered n=**7**, prefix n=7
  - numbered member: `05653c3b-de4b-42fc-bc40-5706934b0589` `model-copy-03-atomicity`
  - numbered member: `16a11904-4a33-4f06-b1f8-c69740113d93` `model-copy-03-direct-id-resolution`
  - numbered member: `7f6563d7-724f-450e-a9bf-12f4454a6f73` `model-copy-02-routing`
  - numbered member: `c5a81e88-9758-489a-8d4a-3b57d4bda766` `model-copy-05-copy-dialog-reentrancy`
  - numbered member: `d34d254c-8b16-45fe-b9b2-b663287cb732` `model-copy-01-semantics`
  - numbered member: `e85d8876-33ab-479e-ae13-e167b9ec1be5` `model-copy-04-copy-dialog-close`
  - numbered member: `eabfeb3e-d747-41c1-acac-2a407a8532e2` `model-copy-02-route-mux`
- `fe-gate` (project `54debc61-f61c-4a59-bc02-2310e560a3a9`) — corrected numbered n=**6**, prefix n=8
  - numbered member: `2961d9f6-ffc5-4eee-8f65-bae50f6d8e27` `fe-gate-03-vitest-econnrefused-noise`
  - numbered member: `2ef21e76-6d87-4a24-87f3-96adf7c5d0c1` `fe-gate-04-vitest-run-cwd`
  - numbered member: `37ce9c2a-ee17-418e-b426-2fc881575e38` `fe-gate-05-token-census-grep`
  - numbered member: `931ce826-7264-4cbf-8ca3-cf3d23e1684e` `fe-gate-06-src-only-commit-not-failure`
  - numbered member: `bb073724-acb1-4bbd-a3b6-6504c67708bd` `fe-gate-02-vite-zero-mod-proof`
  - numbered member: `c94f080a-6322-46de-baf7-3cde7c51b0ea` `fe-gate-07-e8-token-exists-48-not-47`
  - unnumbered-prefix member (extraction None): `6bb2d1e3-4787-4d9c-bb06-7300267984c7` `fe-gate-tsc-baseline-credgroups-18of9`
  - unnumbered-prefix member (extraction None): `9e3080a8-1c16-4a3a-8a4c-58116d3e82d9` `fe-gate-tsc-baseline-parity`
- `skill-evo` (project `54debc61-f61c-4a59-bc02-2310e560a3a9`) — corrected numbered n=**5**, prefix n=5
  - numbered member: `3a77e49d-3d24-429d-8842-303c2309cdec` `skill-evo-03-testpack-ab-deadlock`
  - numbered member: `45ba3e0d-ee6c-490a-8698-92265af8c8c1` `skill-evo-02-testpack-addonly-patch-rule`
  - numbered member: `5a94310b-ce83-4177-b6c4-35225382a8d1` `skill-evo-01-testpack-exitcode-gate`
  - numbered member: `688b9bcc-25a4-47b7-88bc-c6ac25f21d26` `skill-evo-05-testpack-canonical-patch-spec`
  - numbered member: `ce3a2820-6eb1-4b44-b774-56ec9608295d` `skill-evo-04-keeper-missing-deactivate-tool`
- `binding-ttl` (project `54debc61-f61c-4a59-bc02-2310e560a3a9`) — corrected numbered n=**4**, prefix n=4
  - numbered member: `043bc856-1c09-4053-8cea-23f8b0791cc5` `binding-ttl-03-grouplb-test-determinism`
  - numbered member: `7b21cd68-48d2-4492-be58-95acfb71d187` `binding-ttl-01-no-clamp-below-floor`
  - numbered member: `a155efab-9839-4189-a814-0d4fd54f6800` `binding-ttl-02-zero-ttl-normalized`
  - numbered member: `c8128475-895e-4bcc-bfc6-bc48c47d097d` `binding-ttl-03-config-dep-edge-preexisting`
- `prompt-assent` (project `83da04de-a410-4fb5-9e92-251a99d28a52`) — corrected numbered n=**4**, prefix n=6
  - numbered member: `03a6ad0a-3e07-43f2-914e-2b5c17a68d20` `prompt-assent-03-paths-use-assent-not-assist`
  - numbered member: `537b950c-79e0-44bd-960e-9e235a66d3e7` `prompt-assent-04-dm-shield-tool-gate`
  - numbered member: `6b58af57-7a84-4125-ac6d-e3bd55ea8c9e` `prompt-assent-02-category-registration-seam`
  - numbered member: `f91f7c0b-0fef-4a8b-8cc0-e2a117731c5d` `prompt-assent-01-gate-owner-and-evidence`
  - unnumbered-prefix member (extraction None): `6db7e0fd-a716-4463-8e5f-bfea1a118c87` `prompt-assent-flip-on-gate`
  - unnumbered-prefix member (extraction None): `a527d168-c0c3-4af0-90be-24b65895b222` `prompt-assent-pg-server-defaults-gap`

**Priority B — AT the bound (corrected numbered n == 3; one more admission would
refuse): 27 families**

- `skill` (project `83da04de-a410-4fb5-9e92-251a99d28a52`) — corrected numbered n=**3**, prefix n=9
  - numbered member: `0d8b1d7a-a743-4178-9bd3-dd982635a70e` `skill-03-refusal-observability-split`
  - numbered member: `9c6975e7-f091-4584-b578-03e6571e6bb7` `skill-01-off-by-absence-restart-asymmetry`
  - numbered member: `ae25adfe-c162-44af-8b9a-94b69a0dfcd6` `skill-02-evolution-switch-precedence`
  - unnumbered-prefix member (extraction skill-ab-resolve): `0a2a4864-408f-43b4-81dd-109ac1458d21` `skill-ab-resolve-01-guard-parity-all-paths`
  - unnumbered-prefix member (extraction skill-e6): `0df359fe-4a6b-4c93-9cae-816798a97ee5` `skill-e6-04-a4-gauge-grew-not-collapsed`
  - unnumbered-prefix member (extraction None): `83a4b2d3-dd41-4f85-b799-11db50aefaee` `skill-lane-flip-verify-restart`
  - unnumbered-prefix member (extraction skill-e6): `944483d7-0678-434c-a2c6-f6a0f09e003d` `skill-e6-02-sweep-crowns-newer-gen`
  - unnumbered-prefix member (extraction skill-ab-resolve): `bf54c278-c9fd-4aa9-b216-728aebf0b6cb` `skill-ab-resolve-02-svc-sanctioned-unpinned`
  - unnumbered-prefix member (extraction skill-e6): `d5fde27d-8a2f-477e-b400-4faf1edb6216` `skill-e6-03-promote-did-not-resolve-backlog`
- `gc-p4` (project `83da04de-a410-4fb5-9e92-251a99d28a52`) — corrected numbered n=**3**, prefix n=5
  - numbered member: `8820fcd6-9851-45dc-8cd8-4885a55080db` `gc-p4-02-executed-graph-probe-recipe`
  - numbered member: `96c3c897-abb0-4e43-b6dd-f25c3fb5c847` `gc-p4-03-note-drop-turn-end-reminder`
  - numbered member: `ff138b47-ed4b-4fe8-aebb-b1f48ff72700` `gc-p4-01-nudge-gate-executed-graph-green`
  - unnumbered-prefix member (extraction None): `a98aed66-f8f1-41c8-9b81-215cbfd261f1` `gc-p4-cle-line-tripwire-repin`
  - unnumbered-prefix member (extraction None): `ccf336d0-2ff7-4764-a062-8666083e0bfc` `gc-p4-junitxml-is-the-red-source`
- `skillab` (project `83da04de-a410-4fb5-9e92-251a99d28a52`) — corrected numbered n=**3**, prefix n=5
  - numbered member: `07df8535-8389-4f53-9d50-86b683b73864` `skillab-01-zero-traffic-ab-stall`
  - numbered member: `63f64bbe-4f96-45a7-860e-bafb07aa7fca` `skillab-02-keeper-rule-zero-traffic-fix`
  - numbered member: `af8815b5-5623-439d-97d7-973bfe7a5c30` `skillab-03-comparison-counter-semantics`
  - unnumbered-prefix member (extraction None): `47bf5bc7-17ae-45e7-a0a1-4ca2f96d5f2b` `skillab-dev-zero-rows-ro-grant-gap`
  - unnumbered-prefix member (extraction skillab-manifest): `58051960-f3e5-493d-9a9e-fb7f94451052` `skillab-manifest-01-no-consumer`
- `worktree` (project `83da04de-a410-4fb5-9e92-251a99d28a52`) — corrected numbered n=**3**, prefix n=5
  - numbered member: `88d8d1af-df59-4037-9e15-70b4a0570d47` `worktree-03-upstream-auto-set-hazard`
  - numbered member: `b29ef98f-af6c-426d-bdbe-d11f4e1d6e87` `worktree-01-venv-branch-binding`
  - numbered member: `f3fc3c4e-0b7e-4aad-9f88-9c03a913347f` `worktree-02-python-version-divergence`
  - unnumbered-prefix member (extraction None): `8c548d8f-1c52-4109-bc2c-a9543bc2c778` `worktree-deletion-claim-verify-first`
  - unnumbered-prefix member (extraction None): `f28a5c98-7326-44a2-95b3-110f0f288b7f` `worktree-env-fence-shape-recheck`
- `deploy` (project `54debc61-f61c-4a59-bc02-2310e560a3a9`) — corrected numbered n=**3**, prefix n=4
  - numbered member: `c4f21a57-06b0-474e-b2de-12a14e8bf6e6` `deploy-03-smoke-token-15m-daoduc`
  - numbered member: `d19d5149-e2ec-423e-80d6-64670e62efc2` `deploy-01-daoduc-backup`
  - numbered member: `d7043ccb-bf9f-4796-aeea-aa65bd313804` `deploy-02-ensem-dev-primary`
  - unnumbered-prefix member (extraction None): `318928d1-86fc-491f-b0e9-e3f4ea8e96fa` `deploy-authorization-auto-deploy`
- `worktree` (project `54debc61-f61c-4a59-bc02-2310e560a3a9`) — corrected numbered n=**3**, prefix n=4
  - numbered member: `49598f5e-38f2-43d9-b238-c5424b3792c8` `worktree-03-gate-artifact-3way-merge`
  - numbered member: `a696555d-22f0-4068-8ee4-8993d06d491d` `worktree-01-env-fence`
  - numbered member: `ff05895b-7d1f-43f2-962b-15237e4d758a` `worktree-02-upstream-push-hazard`
  - unnumbered-prefix member (extraction None): `cf91010a-ffa6-437e-bc57-d8ecafcc29c1` `worktree-shared-refs-midtask-tip-drift`
- `gc-gate` (project `83da04de-a410-4fb5-9e92-251a99d28a52`) — corrected numbered n=**3**, prefix n=4
  - numbered member: `0a6870dc-77ba-4822-a86c-f03e7925acec` `gc-gate-02-floor-scope-composition-flakes`
  - numbered member: `5ddf201c-4879-478b-a759-5e6d909237f3` `gc-gate-01-fence-family-exclusions`
  - numbered member: `c1346dd6-5078-48cd-94a1-d16ee0c49a01` `gc-gate-03-filter-side-pin`
  - unnumbered-prefix member (extraction None): `1cd099e4-3df3-4715-9cfd-f84a4df588cb` `gc-gate-fence-family-and-xunit2-digest-trap`
- `group-chat` (project `83da04de-a410-4fb5-9e92-251a99d28a52`) — corrected numbered n=**3**, prefix n=4
  - numbered member: `68246f52-81a1-4cb6-8577-b6a1c18a973a` `group-chat-03-p2-typed-collision-fix`
  - numbered member: `7e439bcf-1202-4084-855a-ea678e073e37` `group-chat-01-design-landed`
  - numbered member: `e71966f5-75fa-49f6-ba95-01768d3afaba` `group-chat-02-p1-committed-docs-stale`
  - unnumbered-prefix member (extraction None): `a6e24bfd-79f5-4d16-8b67-42d2a3fbe6b4` `group-chat-p4-nudge-latch-manager-ram`
- `mutation` (project `83da04de-a410-4fb5-9e92-251a99d28a52`) — corrected numbered n=**3**, prefix n=4
  - numbered member: `887dc716-4cd2-4366-b3c0-e2bc41a25e47` `mutation-01-guards-need-counter-mutation`
  - numbered member: `ab081457-5852-4aab-a27c-a750bbe6cb70` `mutation-03-git-restore-byte-exact-proof`
  - numbered member: `af8770ff-08b6-4f16-a861-d1f394248b7a` `mutation-02-worktree-scratch-venue`
  - unnumbered-prefix member (extraction None): `1e461c98-78d0-4674-ae43-b5c9e04d8086` `mutation-proof-must-target-pin-property`
- `buildgate` (project `54debc61-f61c-4a59-bc02-2310e560a3a9`) — corrected numbered n=**3**, prefix n=3
  - numbered member: `2cbf1391-ea89-424a-bbfc-2674815fabf5` `buildgate-03-cachelayer-delta2`
  - numbered member: `2d56cd99-5c9c-4ad6-99cc-39a1fb07bfe0` `buildgate-02-cachelayer-static-green`
  - numbered member: `8c8af3f1-4cdb-4ae7-8b4a-0b46568a408a` `buildgate-01-ensure-critical-static-pack`
- `credgroups` (project `54debc61-f61c-4a59-bc02-2310e560a3a9`) — corrected numbered n=**3**, prefix n=3
  - numbered member: `0543f20f-c0d9-470a-8792-d84b53acd4cc` `credgroups-02-gofmt-zero-drift`
  - numbered member: `30069e28-8c1c-4316-8483-afabcebb9925` `credgroups-01-packa-gate-verdict`
  - numbered member: `642db96c-bb95-42c7-83a4-44407f91c34a` `credgroups-03-gofmt-nonvacuity-method`
- `credgrp` (project `54debc61-f61c-4a59-bc02-2310e560a3a9`) — corrected numbered n=**3**, prefix n=3
  - numbered member: `97ae7590-4734-486a-a0db-ddbbc298eccc` `credgrp-01-m1-dedup-keep-first`
  - numbered member: `a47f8aae-8d5f-48bb-a014-c8be6c43e797` `credgrp-02-census-sql-only`
  - numbered member: `d848e541-782d-4961-b609-1acd31747d52` `credgrp-03-repair-put-reindex`
- `logs` (project `54debc61-f61c-4a59-bc02-2310e560a3a9`) — corrected numbered n=**3**, prefix n=3
  - numbered member: `6b0c55f6-86ff-4a19-b759-1ceb0045030f` `logs-02-no-newlybound-means-one-credential`
  - numbered member: `88763f56-b051-49b5-abf2-f7fc10165249` `logs-01-window-use-follow-to-file`
  - numbered member: `c3332273-2941-4970-ad32-ae16c382611c` `logs-03-cred-id-is-member-id`
- `pipeline` (project `54debc61-f61c-4a59-bc02-2310e560a3a9`) — corrected numbered n=**3**, prefix n=3
  - numbered member: `3e5f43a5-c8f3-43c6-bb0c-bef59c67d0ea` `pipeline-01-merge-master-triggers-image-ci`
  - numbered member: `ae88da6f-feb1-4102-9504-494f719c5992` `pipeline-03-deploy-order-daoduc-backup-first`
  - numbered member: `b78cec9e-e961-4dff-9d3d-f2077dfbbfbb` `pipeline-02-verify-image-tag-digest-feature`
- `baseleg` (project `83da04de-a410-4fb5-9e92-251a99d28a52`) — corrected numbered n=**3**, prefix n=3
  - numbered member: `17782666-d708-4242-b6c3-3dad98590c20` `baseleg-02-worktree-vs-archive-choice`
  - numbered member: `988c8902-f633-4f4a-af17-e1ef71c2b700` `baseleg-01-git-archive-no-dotgit`
  - numbered member: `ec0c0cd6-6a4c-420d-bde3-e1e5dbe4b733` `baseleg-01-phase1-base-red-set-protocol`
- `bindfix` (project `83da04de-a410-4fb5-9e92-251a99d28a52`) — corrected numbered n=**3**, prefix n=3
  - numbered member: `47a9e957-d487-4dd2-b2ac-41b50e4a02ca` `bindfix-02-budget-for-transient-holders`
  - numbered member: `4a4ac838-3522-4774-8b21-7d6b053e9563` `bindfix-03-blocked-boot-zero-startup`
  - numbered member: `ae5c5d2b-4cf9-4bba-81bb-53a7d5d15096` `bindfix-01-prebind-without-listen`
- `discord` (project `83da04de-a410-4fb5-9e92-251a99d28a52`) — corrected numbered n=**3**, prefix n=3
  - numbered member: `33b8d86a-ddfc-4422-9d5a-7677eb9d795c` `discord-01-channel-identity-already-present`
  - numbered member: `4ce6a0ec-150b-4da7-b29d-f5fc6104d6e6` `discord-02-user-origin-arms-by-source-type`
  - numbered member: `ceef3982-96f2-403d-94b2-c67928136729` `discord-03-assent-predicate-fix`
- `disk` (project `83da04de-a410-4fb5-9e92-251a99d28a52`) — corrected numbered n=**3**, prefix n=3
  - numbered member: `18d02e1d-4dc2-420d-b8c1-2268deba9676` `disk-02-evidence-sidecar-manifest`
  - numbered member: `3446af40-1cce-4309-9f51-f359da7da6b8` `disk-01-stage-wt-reclaim-gates`
  - numbered member: `bed25def-a69b-489a-9f62-9966b3208b24` `disk-03-tag-lineage-unmerged-stage-trees`
- `forensics` (project `83da04de-a410-4fb5-9e92-251a99d28a52`) — corrected numbered n=**3**, prefix n=3
  - numbered member: `1ca5cd95-1b5f-41c4-878d-7ca3234d02b0` `forensics-02-maintenance-runs-death-clock`
  - numbered member: `796d5f18-d13e-4445-8623-3b0327cc30ec` `forensics-02-debug-log-evicts-evidence`
  - numbered member: `a5071152-2999-40cf-a49e-2f0a72366347` `forensics-04-registry-truncates-command-text`
- `packconv` (project `83da04de-a410-4fb5-9e92-251a99d28a52`) — corrected numbered n=**3**, prefix n=3
  - numbered member: `0a5f7ac5-e382-42b0-9aca-daabbbeab9b9` `packconv-01-extend-file-owes-no-new-row`
  - numbered member: `4ec06afb-d0bf-464a-aeb1-ea3726900284` `packconv-02-cite-packs-lines-at-base-tree`
  - numbered member: `8075071c-f3a4-465b-b28b-5f35891c39b2` `packconv-03-unregistered-pack-no-coverage`
- `quarantine` (project `83da04de-a410-4fb5-9e92-251a99d28a52`) — corrected numbered n=**3**, prefix n=3
  - numbered member: `235fcba0-a0e8-4a7a-b69b-6fbe36843fa1` `quarantine-01-chart-seal-row-is-stale`
  - numbered member: `52ea474f-141a-41f2-84f0-91915bd1e2cb` `quarantine-01-row-names-all-reds`
  - numbered member: `a17dd744-2be5-447d-a1ba-7941b2303e9c` `quarantine-02-family-rows-need-node-mapping`
- `seal` (project `83da04de-a410-4fb5-9e92-251a99d28a52`) — corrected numbered n=**3**, prefix n=3
  - numbered member: `1e7126c5-2bce-40c5-a282-1688a02cf4e2` `seal-01-restamp-requires-unseal-row`
  - numbered member: `5fcb2598-673e-4121-b5c0-c2e41d19b3ca` `seal-02-repin-dict-keeps-base-value`
  - numbered member: `823d48a9-2c52-44e5-a7de-7c5d0695b66c` `seal-03-repin-mutation-proof-triple`
- `skill-e6` (project `83da04de-a410-4fb5-9e92-251a99d28a52`) — corrected numbered n=**3**, prefix n=3
  - numbered member: `0df359fe-4a6b-4c93-9cae-816798a97ee5` `skill-e6-04-a4-gauge-grew-not-collapsed`
  - numbered member: `944483d7-0678-434c-a2c6-f6a0f09e003d` `skill-e6-02-sweep-crowns-newer-gen`
  - numbered member: `d5fde27d-8a2f-477e-b400-4faf1edb6216` `skill-e6-03-promote-did-not-resolve-backlog`
- `snapshot-l3` (project `83da04de-a410-4fb5-9e92-251a99d28a52`) — corrected numbered n=**3**, prefix n=3
  - numbered member: `08580dbd-f343-4a1b-b329-c5f0d24f243b` `snapshot-l3-01-malformed-json-empty-collapse`
  - numbered member: `81447a12-7a29-463e-a0c2-514d9039c1fd` `snapshot-l3-04-drift-pin-identity-hazard`
  - numbered member: `d31fa038-69b7-4690-aa17-5a4817c007d6` `snapshot-l3-02-base-red-set-one`
- `testgate` (project `83da04de-a410-4fb5-9e92-251a99d28a52`) — corrected numbered n=**3**, prefix n=3
  - numbered member: `28fbf220-25dc-48f2-877e-9a398a9eeeec` `testgate-01-explicit-paths`
  - numbered member: `31c5ad09-76e6-4c96-82a6-cb87cdb762ad` `testgate-03-double-drift-hides-branch`
  - numbered member: `c6a7a4ec-02c1-4802-bf3c-3488c8025b9b` `testgate-02-standalone-packs`
- `wiring` (project `83da04de-a410-4fb5-9e92-251a99d28a52`) — corrected numbered n=**3**, prefix n=3
  - numbered member: `0e3dd745-ee9c-4c43-bc95-c49e1159fd28` `wiring-01-capture-dispatcher-seam`
  - numbered member: `c9dbbdc0-f3e7-4c3c-ab81-1e9ea9f878a4` `wiring-03-grep-scoped-miss`
  - numbered member: `ec808433-cd65-4076-95f5-7593f96e0906` `wiring-02-phase2-window`
- `verify` (project `d5ef370a-d517-476e-9cbd-ce59b7229e8c`) — corrected numbered n=**3**, prefix n=3
  - numbered member: `0133757c-ff63-49e0-972c-681f04bce3ff` `verify-03-jsonpath-null-trap`
  - numbered member: `44490f84-b650-4639-89bf-631d26b6866b` `verify-01-g8-maxtokens-512`
  - numbered member: `782238c3-bca9-4240-86c4-58c8eea95efd` `verify-02-registry-api-digest`

**Gate truth to read with the map.** The live refusal condition is
`count_active_in_family >= 3`, i.e. the **prefix** count — not the numbered count.
A family whose prefix n is already >= 3 refuses its next numbered admission even if
its numbered n is below 3; conversely a family whose prefix n is below 3 but whose
numbered n is >= 3 would refuse only once the unnumbered rows are counted in the
same project. Both numbers are listed above so the PM can adjudicate on the real
gate quantity.

**Prefix inflation.** The prefix count is a `startswith` count, so a SHORT family
absorbs the rows of every LONGER family that shares its prefix — see §3d.2 for the
row-level list. A short family can therefore read `prefix n = 9` while owning 3
rows of its own, and it would refuse on the `>= 3` prefix test even though only
its own 3 rows are gated. The corrected key does not change this (the count is a
`startswith` count under both keys); it changes WHICH rows are gated members, so it
is the numbered count that decides how much of the pressure is real.

**Render cost — why WIDE families are the wave's first target.** The `model-copy` family
holds **7** ACTIVE members (project `54debc61…`) and that project holds **1** pinned row
(`pipeline-03-deploy-order-daoduc-backup-first`) against the 2-per-project pin cap
(`POK_PIN_CAP_PER_PROJECT = 2`) — the caller's shorthand is that `admit_view` therefore
admits **~2 of ~11 candidate rows**. Machine-checked structural facts from the raw
census: `model-copy` is 7 rows with 0 of its own pinned; the pin cap is 2 PER PROJECT;
and under the CORRECTED key `model-copy` becomes ONE ATOMIC family unit (`admit_view`
admits a family whole, against an 8-entry / 2,900-char block budget) — so a wide family
is not merely many rows, it is one indivisible unit that can consume most of the block.
The caller's exact admitted/omitted split is NOT re-derived here (`admit_view` is
audience- and rank-dependent); the 2/11 figure is quoted WITH attribution. Wave
priority: shrink the wide families first.

---
*Generated 2026-11-18T21:12:38Z by the dual-key census generator (python3 stdlib) from the read-only
snapshot `/tmp/pok_census_raw_20261118.json` (sha256 `d8a6e80bbc417530ac6d0e1ea8ba454e6fe3314b7a49e0e02b041054360b8647`); artifact written to `/home/nea/ensemble-src-wt-pok-family-key/.agents/shared/planning/pok-sweep/dual-key-census-2026-11-18.md`.*
