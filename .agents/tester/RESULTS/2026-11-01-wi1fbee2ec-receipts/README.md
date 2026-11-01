# wi-1fbee2ec functional-gate receipts archive (2026-11-01)

- M1..M8/: dev mutation receipts, minted 2026-11-01T16:12:02-16:13:27Z at tree 701c6c27e (PRE-tidier:
  918cdec7e 16:37Z + aa5f2cd6c 16:39Z landed after minting). Snapshot files are PROVENANCE ONLY.
- The gate's independent re-proof (8/8 PASS) used HEAD-git-object baselines for M1/M3/M5/M7/M8
  (tester adjudication "Option A": stale-snapshot restores would have run GREEN legs at receipt-era
  code; drift diff proven non-executable — see gate-evidence/wi1f-g3-drift-evidence.txt) and the
  receipt snapshots verbatim for M2/M4/M6 (child_reports.py byte-identical at HEAD).
- harness/: the re-runner (run_receipt.sh) + run_mutation.sh + m1..m8.py + backup.py, ADAPTED from
  /tmp/wi1fbee2ec-mut (WT= repointed to the isolated mutation worktree /home/nea/ensemble-src-wt-obligation-mut).
  Resolves the claimed-but-uncommitted discrepancy: the original existed only in /tmp (git log --all
  empty by path and -S); committing it here makes the claim true and durable.
- gate-evidence/: per-lane run logs (G1/G2 base+head, G3 legs, G5 packs, G7, G9 driver+accounting+
  disposition probes, G10 legs). PG password material excluded/redacted.
- Authoritative summary: ../2026-11-01-wi1fbee2ec-functional-gate.md
