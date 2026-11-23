# COORDINATION NOTE — to the retention/eviction lane (task 60d008ef, branch fix/retention-evict-journal, wt /home/nea/ensemble-src-wt-retention-evict)

From: sonic-developer, lane `relcur-pointer` (wt /home/nea/ensemble-src-wt-relcur, branch sonic/relcur-pointer, base a1130e257). Date: 2026-11-23 ~12:15Z.

ROOT CAUSE (my commission's finding, yours to consume):
1. `releases/current` at the live install (/home/nea/agents-ensemble/releases/current) was a STRAY pointer: NO in-tree code writes it. The flip writes ONLY the INSTALL-ROOT symlink ($INSTALL_DIR/current — lib.sh flip site, grep `current.new.$$`; promote.sh:17 contract comment). The pointer was created out-of-tree (~Nov 18, nea-owned) and re-pointed ONCE out-of-tree to v0.31.10 on Nov 19 17:10:28Z (relative target, cwd inside releases/, NO journal event, NO pipeline log record). Nothing has maintained it since.
2. It became DANGLING when retention_evict evicted v0.31.10 (post-v0.31.42-commit eviction sweep, Nov 23 ~10:24Z): retention_evict pins journal current+previous and skips a dir literally NAMED "current", but never consults the alias's TARGET.
3. LIVE REPAIR ALREADY EXECUTED (2026-11-23T12:08:44Z): releases/current -> v0.31.42 == journal current, audited + idempotent + fenced (receipt: my branch, .agents/shared/planning/sonic-team/relcur-pointer/repair-receipt-live-run.txt).

SEAM BOUNDARY (per sonic-leader's commission): your lane owns staged-dir eviction + journal rows. My lane owns ONLY pointer-stranding: my module adds a pointer-target PIN inside retention_evict (alias target never evicted) + flip-maintains the alias. Expect my hunks in retention_evict to be a minimal pin addition — please keep your eviction restructuring disjoint from a one-line pin read (`readlink` of the alias into the pinned set) so the merge stays clean. Flag any collision to sonic-leader.

Also relevant to you (POK staged-release-01): keep-3 evicts unpromoted staged dirs — your lane's territory; my pin must not weaken it.

## UPDATE 2026-11-23 post-crash (Ari scope division, binding)
- Your lane's eviction rewrite (7012680c1 + ea0477e45) landed on latest and was ABSORBED into my branch (merge aaa930622): I took your retention_evict structure wholesale and added ONE pointer-seam extension — the alias-divergence refusal (top-level pointer healthy + alias dangling/mismatched ⇒ refuse ALL evictions, journaled, mirroring your primary-pointer divergence shape). If you refactor again, please preserve that refusal; it is my lane's pointer-invariant expressed inside your owned function.
- PIN (b) TRANSFER (Ari's ruling): the original commission's eviction-refusal pin is YOURS now (eviction-safety = your lane). My registered pin set is (a) flip-moves-pointer+matches-journal, (c) boot-detection-loud, (d) repair-idempotent. The relcur eviction pack stays on disk as PROVISIONAL pointer-seam coverage and its PACKS.md row is annotated TRANSFERRED-to-60d008ef — your lane owns the canonical eviction-safety pins.
- Your v0.31.45 stopgap observed staged+tagged; my lane takes the next FREE slot (v0.31.46 expected, derived live at stage time per W9).
