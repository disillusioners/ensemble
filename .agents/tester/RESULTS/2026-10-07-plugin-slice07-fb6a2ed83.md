# Plugin Subsystem Slice ⑦ — Independent Test Pass @ fb6a2ed83 (MCP retirement — the overnight build's final slice)

- **Date**: 2026-10-07, completed 05:22 UTC
- **Commission**: slice ⑦ (MCP retirement) — report-only; the :8079 dev daemon boots FROM worktree-07 and stays RUNNING (verified-against, never restarted)
- **Worktree**: `/home/nea/ensemble-src-wt-plugin-subsystem-07`, branch `feature/plugin-subsystem-07`, HEAD `fb6a2ed83e4ea10218232604d6a32e6a4efc8299`, base `da81473d5` (2 commits: `b71eaaa49` seam retirement + `fb6a2ed83` smoke one-pager; 32-file diff all in retirement scope, zero live-install paths)
- **Workers**: 1c19528f (sentinel + suites) · 7b886250 (log/carve-out/fail-closed/boundaries) · afa7f0db (live spawn + envelope + other-MCP)
- **Overall verdict**: 🟢 **GREEN — 6/6 checks PASS.** GREEN + reviewer APPROVED = ⑦ merges, then staging. The overnight build's test coverage is complete.
- Code changes by tester: NONE (the daemon's own install-audit.jsonl writes during the window = its normal kms_issue audit behavior, not tester action; HEAD unchanged throughout)

---

## Per-check verdicts

| # | Check | Verdict | Evidence |
|---|---|---|---|
| S7-1 | Retirement sentinel | ✅ 3/3 | `tests/unit/test_opendesign_mcp_retirement_sentinel.py` — zero-refs-outside-carve-out / carve-out-set-exactly-3-files / vacuous-allowlist-tripwire (each file must STILL carry Apache-2.0). Negative probe: scratch injection **unsupported** (hardcoded `daemon/` root, no env/fixture indirection) → **read-verified** the detection loop verbatim (needle `open-design-mcp` ≠ `opendesign`; depth-bounded 3-part carve-out) — documented per the fallback contract |
| S7-2 | Suites | ✅ (commissioned minimum) | **plugin_subsystem 563/0** (unchanged from ⑥) + **sentinel 3/0** = component **566/0**. The "1067/17" full selection **not reproducible under the 5-min cap** (tests/unit+services → exit 124 at ~14%; narrower: 11F/1166P; services alone 11F/600P) — commission fallback accepted. **The fixture-error family PRE-EXISTING, proven**: `TestRecordMetricsWiring` (`SimpleNamespace` lacks `.priority` at task_processor.py:552) — same 10 test ids fail at base `da81473d5` and HEAD (base-state rerun via temp-worktree CWD-shadowing; cleanup proven). Claim-count 17 vs observed 10-11 = broader selection, same family |
| S7-3 | Restart evidence | ✅ ×4 | (a) log: **0** `open-design-mcp`; 1 sanctioned `opendesign` = B-plugin boot-scan (the NEW system). (b) designer `7487dae3` spawned live: **exactly 4 native od.\*** bound via `DYNAMIC_TOOL_NAMES`, **zero `mcp_opendesign_*`** — set-computed over the `Filtered tools: 215 → 65` DEBUG line; MCP load = 2 context7 tools; pool = context7 only. (c) honest-framing envelope: **committed at HEAD** in `.agents/coder/RESULTS/2026-10-07-slice-07-smoke.md` §4.2 (typed `finish_reason: length` + `error_code: empty_response` + "completeness gate refused… html_bytes=0"; truncated output deliberately NOT piped to lint — halt path parity-gated at ④b). Current verification boot carries no LLM traffic by design → location note surfaced. (d) daemon/ grep = **exactly 4 hits, all Apache-2.0 attribution** in `plugin_subsystem/opendesign/` (the correct §4(d) container; commission's "builtin-server module" wording pointed at the other file) |
| S7-4 | Other-MCP intact | ✅ w/ caveat | **context7: bound + healthy** (2 tools, pool warm 1/1, live npm process under the daemon). plane: registered-but-degraded this boot (row-config validation + remote SSE unreachable) — **pre-existing family** (identical in the ⑤b boot; attributed in the ⑦ record to the dev-env flake). No harmless ping surface existed; presence-level verification |
| S7-5 | Boundaries + branch | ✅ | **OD daemon pid 3288819 @ 7456 STILL RUNNING** (deferral honored; env decommission = morning ceremony); 9797/7979 untouched; :8079 CWD-verified from worktree-07, never restarted; `systemctl --user` unavailable in this env (no DBUS — OD runs as plain node process; pid evidence captured); HEAD exact, 2 commits, porcelain = `?? .venv` only |
| S7-6 | Fail-closed clause | ✅ | **ZERO** `mcp_opendesign_od_*` / `od_generate_design` / … tool-traffic markers across the entire post-restart log window; warm pool lines verbatim: context7 only; no opendesign entry in pool/schema-discovery/tool-adapter surfaces |

## Notes for the leader / morning ceremony
1. 🟢 **⑦ merges → staging** on your APPROVED. The retirement is live-proven: 4-native/0-MCP on a real designer, zero residual MCP traffic, sentinel tripwires armed (incl. the vacuous-allowlist guard).
2. ℹ️ Plane's dev-env degradation (row-config + unreachable remote SSE) is a standing pre-existing ticket candidate if plane binding is ever needed live on this host.
3. ℹ️ The "1067/17" claim was not exactly reproducible within timeout discipline — component counts + family pre-existence carry the gate; if you need the full selection number, it requires a >5-min run (split-pack territory).
4. ℹ️ Reusable verification pattern banked: the `Filtered tools` DEBUG line + set computation proves native binding without LLM turns; the sentinel's hardcoded-root design means future negative probes are read-verifications (or a small refactor to inject a root).
5. ℹ️ Morning ceremony reminders from earlier gates: ⑥'s staleness pre-decision (copy_freely trail evidence or journaled `--allow-stale-plugins`); the OD env-file decommission + daemon stop (pid 3288819); the stale `install-mermaid-cli.md` working-tree copy cleanup; manifest-1.0.3 amendment rides the ⑥ merge.

## Boundary compliance
:8079 verified-against, never restarted · 9797/7979/8088 untouched · 7456 alive-and-untouched (deferral) · no systemd mutations · no key changes · no live promote · zero tester modifications/commits · scratch /tmp only (temp base worktree created + removed with proof).
