# P6 gate lesson: census-pin sibling-miss + retired-needle docstrings (2026-11-16)

Two net-new-red mechanisms from the group-chat P6 tester gate (evidence:
`RESULTS/2026-11-16-group-chat-p6-tester-gate.md` §1, `/tmp/gc_p6_gate/digesteq/`):

## 1. Census-pin sibling-miss (net-new #1–4)

When a lane shifts a GLOBAL census (route count, mount count, router-family
count), every pinned copy of the old numbers must be updated IN THE SAME LANE.
The P6 lane updated the two manifest sites it knew about
(`test_router_scope_manifest.py` → 119/40, `test_router_class_manifest.py` → 36)
but missed the THIRD copy embedded in `test_metrics_query_scope_cage.py`
(`DECLARED_ROUTE_CENSUS=117 / MOUNT_CENSUS=39` + docstring counts) — 4
deterministic reds at gate time.

**Rule for coding lanes:** after any census-shifting change, grep the WHOLE test
corpus for the old triple (`117`, `39`, `35` as a set), not just the files you
edited. The cage files duplicate manifest constants by design (three-way form).

**Rule for tester gates:** the net-new ladder must attribute this class as
LANE-ATTRIBUTABLE (green at base, FFF at head, cage file byte-identical to
base ⇒ data-side reach), NOT quarantine it — the fix is a pin update, not a
flake.

## 2. Retired-symbol needles in NEW-file docstrings (net-new #5–6)

A lane-ADDED file (`daemon/services/context_block.py`) whose module docstring
honestly documents the RETIRED surface (`completion_check_note`,
`_make_completion_check_note_message`) trips two whole-tree guardians
(the lca-check whole-tree census and the attestation negative pin) — literal
needles, classified LIVE ("daemon" files have no witness exemption).

**Rule:** new daemon files documenting retired symbols should re-word to avoid
the LITERAL retired identifiers (e.g. "the completion-check note surface was
removed" without the exact constant name), OR the lane must add the file to the
guardian's witness exemption set. Docstring honesty about removal is good —
needle-free wording is better.

## 3. Operational (minor)

- The tier-2 ladder is a SERIAL per-node x3 probe — liveness of a long ladder
  is judged by node turnover + growing ledger lines, NOT per-PID CPU (PIDs are
  ephemeral per node). See KV `gc_liveness_probe_serial_ladder_signal`.
- A watchdog "hang" on a long gate leg was a false positive (artifact-proven
  ALIVE) — second occurrence of the P5 lesson; never terminate on
  message-timestamp evidence alone.
