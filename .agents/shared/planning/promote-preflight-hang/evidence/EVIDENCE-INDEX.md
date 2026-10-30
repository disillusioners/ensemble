# Evidence index — promote-preflight-hang wave 2 (phases 0 + 1)

Every artifact below is stable on disk and sha256-indexed. Committed artifacts are
at `fix/promote-preflight-hang` = `7879be900` (phase 1) on `1f094f768` (phase 0),
base `b1183a637`. Uncommitted-by-design: this plan dir (final wave owns the docs commit).

## Pack runs at HEAD (all re-run post-commit, 2026-10-30)

| Pack | Result | What it proves |
|---|---|---|
| `tests/packs/preflight_journal_parity_test.sh` | **60 passed, 0 failed** | 27-fixture old-vs-new parity (BASE oracle extracted from git); verdict + stdout bytes + stderr class; duplicate-key BASE-bug-preserving; G5 processed-bytes pin (BASE refuses / helper refuses / raw-byte scanner ACCEPTS — mutation-meaningful); locale pair C/C.UTF-8/en_US.UTF-8; single-open structural + 200× atomic-replace race; placement invariants |
| `tests/packs/preflight_census_test.sh` | **9 passed, 0 failed** | Derived census == committed (25 direct sites); HEAD call sites unchanged; per-char walks legacy-only (SC3); TWO-SIDED unreachability proof; planted-walk mutation |
| `tests/packs/preflight_anchor_index_test.sh` | **20 passed, 0 failed** | All anchors line-exact at b1183a637 + construct-present at HEAD + 16 new constructs + non-export pin + mutation leg. Spawn-count watcher: max 3 concurrent, converged in ~1.5 s |
| `tests/packs/preflight_journal_scale_test.sh` | **12 passed, 0 failed** | SC1: `journal_read` **149 ms** on the 455,783 B fixture; update 598 ms; sub 405 ms; scaling flat; guard never consulted on the O(N) path |
| `tests/packs/preflight_size_guard_test.sh` | **24 passed, 0 failed** | G1/G2/G3 boundary trio; size-named refusal + remedy pointer; journal byte-unchanged at lib-level refusal; Q3 warn+continue; 120 s attempted-then-bounded (no fail-fast); 3-class matrix; threshold mutation |
| `tests/packs/preflight_repro_test.sh` | **7 passed, 0 failed** | BASE rc=124 @ 60 s budget, zero stdout (the symptom); HEAD 84 ms whole-journal; stdout == processed fixture bytes |
| `tests/packs/preflight_shasum_batch_parity_test.sh` | **4 passed, 0 failed** (default) + `--full` re-run | byte-parity synthetic/agents/frontend/plugins; misalignment fallback; timings below |
| `tests/packs/preflight_base_scale_measure.sh` | one-off (not a gate) | bounded k·N² protocol; see BASE-SCALE-20261030.md |

## Files → sha256 → claim

| Path | sha256 | Claim |
|---|---|---|
| `evidence/BASE-VERDICT-INDEX-27.txt` | see file header | 27 fixtures × (input sha256, input bytes, BASE rc, BASE stdout sha256/bytes, stderr class) frozen from the git-extracted oracle 2026-10-30T01:48:29Z |
| `evidence/BASE-SCALE-20261030.md` | (this dir) | k = 1.644e-8 s/B² (±0.6% over 4× range); EXTRAPOLATED T_user(455,783 B) = 3,415 s ≈ 56.9 min — labelled, never run |
| `evidence/shasum-full-20261030.log` | (this dir) | plugins BASE 303,774 ms → batched 1,988 ms (parity OK, 4,930 files); agents 19,613 → 283 ms (338); frontend 34,528 → 332 ms (567); synthetic 13,140 → 199 ms (245); misalignment fallback fires |
| `tests/fixtures/journal/INCIDENT.sha256` | (committed) | `06ebaac3eb9b881b9ff550c6adca591941158eaf87e5d6bc5a90ec98a2398c6c` — incident fixture 455,783 B / 2,949 entries / 2,775 mcp_server_state; regenerated at pack runtime, asserted loud on drift |
| `scripts/upgrade/journal_splice.py` | `50c406a4769025661bb76c006869d274f39967edb0a182342380798d0a8eb84b` | BYTE-UNCHANGED vs `git show b1183a637:scripts/upgrade/journal_splice.py` (R4; re-asserted by parity pack §G at every run) |
| `scripts/upgrade/journal_read.py` | (committed @ 7879be900) | the O(N) read-only helper; three distinct algorithms; rc 0/1/2/3/4 |
| commits | — | phase 0 `1f094f768` · phase 1 `7879be900` · diff stat: 46 files, +2,932/−21 |

## HEAD measured numbers (this host, ambient ~2 cores loaded)

* `journal_read` 455,783 B: **149 ms** (lib.sh path incl. probe) / **84 ms** (helper) — SC1 budget 5,000 ms
* `journal_update` full RMW: **598 ms** · `_json_sub` full journal: **405 ms**
* BASE (bounded samples, user CPU): 56,640 B → 53.38 s; 111,072 B → 203.16 s; 221,184 B → 804.09 s
* Guard trip at the default threshold on the legacy class: sub-second (size-guard pack G2, scaled leg)

---

## Wave 2 — phases 2-4 (2026-10-30), HEAD = c20703b72

Commits: `262c8994d` (phase 2) → `d48c0e0ca` (phase 3) → `c20703b72` (phase 4),
on `7879be900` (phase 1). `journal_splice.py` remains BYTE-IDENTICAL
(sha256 `50c406a4769025661bb76c006869d274f39967edb0a182342380798d0a8eb84b`
== `git show b1183a637:scripts/upgrade/journal_splice.py`).

### Pack results at HEAD (all re-run post-commit, 2026-10-30 ~04:00Z)

| Pack | Result | What it proves |
|---|---|---|
| `tests/packs/preflight_watchdog_test.sh` | **38 passed, 0 failed** | SC4 breach shape+order (live-shaped path + release-first mutation RED), L1 phase breach, SC4b unlocked split (status.sh exits 1; rollback/restart 78 + zero rows), RuntimeMaxSec derivation 1320/1620/1920/floor + kind scoping + live soak refusal (real promote.sh), SC5 structural exclusion + 3 healthy runs, cgroup-TERM invariant probe, double-TERM guard + mutation, override value-in-force, wedged systemctl loud |
| `tests/packs/preflight_real_soak_test.sh` | **5 passed, 0 failed** | SC5 REAL 300 s conservative soak (real sleep loop + real HTTP /livez+/readyz probes, T0→T1 300 s, rc=0), 0 halt rows, 0 deadline-breach rows, lock released |
| `tests/packs/preflight_logging_test.sh` | **30 passed, 0 failed** | SC6 survival (SIGKILL), SC7 ≥6 markers, file-sink one-copy + legacy byte-identical, write-failure stderr+journal row, 4 KiB cap/elision/one-line + ≥8 KiB corruption scanner, two-file sink + pointer-by-run_id + env scope + reader fallback, R7 fail-closed pin (file-sourced signature ⇒ refusal; signature-free ⇒ journal-truth), EPIPE sites, R8 retention (20 kept for the running owner + 5 fences + age cap), redaction |
| `tests/packs/preflight_stage_lock_test.sh` | **13 passed, 0 failed** | SC8 SIGKILL orphan repro, both reclaim paths with all five fields, live-owner never broken, explicit success release + silent double-release, live-shaped fence interop |
| `tests/packs/preflight_journal_parity_test.sh` | 60 / 0 | unchanged parity (27 fixtures) |
| `tests/packs/preflight_census_test.sh` | 10 / 0 | 25 BASE sites + 1 documented phase-3 read site |
| `tests/packs/preflight_anchor_index_test.sh` | 42 / 0 | base line-exact + HEAD constructs (+ relocated alias, phase-2/3 constructs, trap-order line check) |
| `tests/packs/preflight_journal_scale_test.sh` | 12 / 0 | SC1 unchanged |
| `tests/packs/preflight_size_guard_test.sh` | 24 / 0 | guard trio unchanged |
| `tests/packs/preflight_repro_test.sh` | 7 / 0 | BASE hang reproduces, HEAD 84 ms |
| `tests/packs/preflight_shasum_batch_parity_test.sh` | 4 / 0 | batched manifest byte-parity (incl. the chunked walk) |

### Artifact shas (wave-2 phases 2-4)

| Path | sha256 |
|---|---|
| `scripts/upgrade/lib.sh` | `68eb0ad169a354fcff3a0b077c010cf0500b6a171868714a16c8c8a6dc95a9c2` |
| `scripts/upgrade/promote.sh` | `248c018f4a005960f16531ef6c0ec42aed9df4001562588ad4dcd81b0aaed541` |
| `scripts/upgrade/stage.sh` | `2716679ec9ff401d10f6c77cb95e810c8d6ff81f217f667d54ddb2e9a91458ee` |
| `scripts/upgrade/status.sh` | `96abfde11e45755f086b0ca64f5e837edc60063d98b3d8b5b1b36d0bdd8cd8e6` |
| `daemon/tools/upgrade_journal.py` | `0cf9d1d77e9a1af254298cc0eac101aae9bc030a0357438f2f78622dea36e660` |
| `daemon/tools/upgrade_tools.py` | `5d4ba6783eb136f54e8c45994f248133f97e91c3065ef2a193ca69464472fdab` |
| `daemon/services/upgrade_journal_sweep.py` | `5695afa33c1eb340d3e23a3de2552bc0990cf5d59b644168b3a198ceafaa8d35` |
| `daemon/manager.py` | `2d766dfd9da5c46147a83cb321d6d3b1244bf40e0c3e1b0304cd8794f9b4d51d` |
| `tests/packs/preflight_watchdog_test.sh` | `74ad9e77e21458953083a321dd7d45143540f1c8fd6990e4c660b9b94eb02769` |
| `tests/packs/preflight_logging_test.sh` | `c459b9bc9bec82f6aea760ae8507a9f012dcde58ff832e8b9a4435e3446d2cd5` |
| `tests/packs/preflight_stage_lock_test.sh` | `575ac7c88d0c2787a58ce74ed2f0b67540fd08f859f4421b845335d7d31b85a7` |
| `tests/packs/preflight_real_soak_test.sh` | `967765b606c31e6305195084a0302b188e06675b2a6b7f2577319f3ac850da95` |
| `tests/packs/preflight_anchor_index_test.sh` | `34f35c76673f1743d186ededd141e3b55d7b7dff8fd560bd863dab64a7c15a1f` |
| `tests/packs/preflight_census_test.sh` | `91ce7e9f531e8de57e0563126e66ce5eac66dad219bf70327c30ed86e0c6676c` |

### Measured budget derivations (every number traced)

* `PREFLIGHT_STEP_BUDGET_S=120` — 2.3× the longest measured preflight step
  (`integrity_verify` ≈ 26 s/call pre-D1, 52–60 s of the 68 s healthy
  preflight across 2 calls; evidence/CODE-PATHS.md §12).
* `PREFLIGHT_PHASE_BUDGET_S=300` — 4.4× the measured 68 s healthy preflight;
  < the daemon's 660 s benign-detach observation (`upgrade_journal_sweep.py:102`).
* `RuntimeMaxSec` = max(1200, `PROMOTE_TOTAL_DEADLINE_S` 900 + `SOAK_S` 300 +
  120) = **1320** (svc. > the 900 s ceremony deadline; plan worked example
  "1800 @ SOAK 600" contradicts the formula — formula governs, D-9).
* `STAGE_TREE_BUDGET_S=60` — unchanged from phase 1 (30× the 2.0 s batched
  plugins walk; 150× below the 304 s per-file pass).

### Fence disclosure (lane report)

An ad-hoc NON-pack debug invocation at 2026-10-30T02:43:32Z ran
`promote.sh live` without HOME isolation; `resolve_env live` resolved the
REAL install root and appended ONE `refusal` row (`reason=not-staged`,
target `vnone`) to `/home/nea/agents-ensemble/releases/state.json`. No lock
was left, no state field changed, the live daemon was untouched (livez
0.27.12, uptime continuous). The row was NOT removed (journal is
append-only; hand-editing live state is the fence's own prohibition). The
packs themselves are fenced (FAKE_HOME ⇒ `resolve_env live` resolves into
the scratch — proven by D5's scratch-journal refusal in
`preflight_watchdog_test.sh`).
