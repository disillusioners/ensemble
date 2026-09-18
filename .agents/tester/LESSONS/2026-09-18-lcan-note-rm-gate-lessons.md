# LCA note-rm gate lessons (2026-09-18)

## By-package import deletion vs per-symbol liveness (CRITICAL — the gate blocker)
6a695b8f deleted the whole `from .context_messages import (CONTEXT_KIND_CHILD_REPORT_CHECK, _make_context_message, _resolve_tree_root_id, _stable_id_for)` block on the assumption all four names were mint-side-only. `_resolve_tree_root_id` had a SECOND caller in the SAME file (child_reports.py:4319, `_dispatch_post_commit_side_effects` — body byte-identical across the delta). NameError was silently swallowed by the surrounding try/except (DEBUG-only log) → lifecycle-hook context_key silently fell back to instance_id. Detection needed the Set-B delivery-plumbing family pack — the dev's 306-test delta-scoped run never loaded this file. LESSON: (1) before deleting an import block, grep the DELETING FILE for each symbol, not just the feature's call sites; (2) silent try/except around name resolution hides NameError at DEBUG — a missing-import contract test (import-liveness smoke) would have caught this at commit time; (3) whole-tree family packs (grep -rln <module> tests/) are the safety net for "unrelated path" collateral.

## Vacuous `-m integration` probe trap
The 13 integration-hang proxy files carry ZERO pytest.mark.integration marks — `-m integration` deselects 100% of them, producing a green vacuous probe (rc=5-ish no-tests path). Probes must verify N>0 selected (`--collect-only` count) before trusting a marker filter.

## Pre-removal advisory false positives (live demos, ×2)
The production daemon (v0.13.3, pre-removal) minted Child Report Check advisories on THIS gate's own worker completion reports twice: (a) matched the literal string "Awaiting issue?" in an evidence-table header; (b) matched the test fixture string "I will write RESULTS. Ending turn" quoted as evidence in a report. Both were false positives under content adjudication — exactly the delivery-time substring-heuristic noise the removal eliminates in favor of judge-based evaluation.

## Misc
- Boot-smoke mock role detection needs per-agent key widening when agents get renamed (developer → "code orchestrator").
- Unmarked integration files + default addopts: run WITHOUT -m integration; the repo's heavyweight attestation integration files carry no marks.
- Idle-orphan b08f40fe + revive_after_escalation share the live-judge verdict-variance flake class — hermetic judge-verdict pins are the durable fix.
