# LESSONS — v0.26.2 ruling-e2e commission (2026-10-21)

## 1. "Silent worker ≠ dead work" — harvest /tmp before re-dispatching
Both GT-b workers went watchdog-silent (1h36m, 1h22m) while their DETACHED background bash drivers kept working to completion. The instance's `last_activity_at` measures LLM turns, not the work. The 29k-node HEAD census was 100% complete on disk when the second worker looked dead. **Playbook: on hang-notice, inventory the lane's /tmp artifacts (progress.tsv, driver logs) BEFORE terminating or re-dispatching — the report may already exist as files.**

## 2. `terminate_instance` does not reap setsid'd descendants
The terminated GT-b worker's driver chain (`run_tree.sh` → `resolve_unknown.sh` → `run_base.sh`) survived termination and kept launching phases for hours. Benign here (write-confined to /tmp), but any lane that setsid/nohups long runners leaves orphans that: (a) consume CPU, (b) can CONTAMINATE later test runs in the same worktree (see #4), (c) may surprise a future lane. Disclose-orphan-drivers in reports; check `pgrep -af <worktree-tag>` at lane start AND end.

## 3. "Whole-tree" framing trap — derive corpora from the diff, not from inherited figures
`tests/` collects **~28,931 nodes** (tests/unit alone 17,858). The dev-gate figure "1114P/2F" is a bounded selection, NOT the whole tree. A dispatched expectation figure that doesn't match a derivable corpus is a framing error on the dispatcher (mine). GT-a's method — corpus = `git diff --name-only BASE..HEAD -- tests/` — is the correct derivation and it falsified nothing.

## 4. Load contamination produces rc=124 "hangs" that are not hangs
7 tests that "timed out" during the loaded 29k run pass in 1-3s isolated. The perf-matrix `[1x10k]` cell needs ~281s of a 300s cap — **a 300s timeout over a 281s baseline is a false-positive generator**: any concurrent shard/sibling pytest pushes it over. Scale perf-cell timeouts ≥600s or gate on load quiescence. Also: BASE-vs-HEAD comparisons must control for MACHINE LOAD, not just command shape — "passes at base, hangs at head" was contamination, not regression.

## 5. Broad-tree collection traps (both hit, both documented)
- `tests/packs/*` — module-level `sys.exit` guards → INTERNALERROR aborts the whole `--collect-only`. Mandatory: `--ignore=tests/packs`.
- `tests/e2e/test_context_injection_hybrid.py` — import-time GET :8079. Collection IMPORTS modules even when marker-deselection would skip their tests. Mandatory: `--ignore` it too (or accept rc=2).

## 6. Census numbers: name the population before citing a count
"14 rows" turned out to be TOTAL source_configs (11 scheduler + 3 chat-platform). The load-bearing claim (0 reuse_instance) held under both readings, but the count needed population-naming to reconcile. Same imprecision lives in a shipped code comment (adapters/scheduler.py narrowing comment) — cosmetic, noted.

## 7. Boot-smoke isolation is now proven-cheap
`systemd-run --user` transient unit (needs explicit `XDG_RUNTIME_DIR=/run/user/1000` in agent shells) gives cgroup-level proof of isolation for ~zero cost — quote `/proc/<pid>/cgroup` in evidence. The kill-switches remain mandatory regardless (defense in depth).

## 8. Doc-text surfaces need pins
D-V2 (3 stale "FRESH instance" strings in self_alarm.py `_full_doc_`/Field descriptions) survived because the ruling pack pinned message+mapping but not the agent-visible doc text. When a ruling mandates "zero <X> text anywhere", the pin must cover `_full_doc_`, `args_schema` Field descriptions, and error envelopes — and the sweep must merge adjacent string literals (`re.sub(r'"\s*"', ' ', text)`) because plain grep misses split phrases.
