# sonic-guidelines-shakedown results (2026-11-13)

TEST: FAIL — 6 claim-level mismatches (F1–F6) against ~120 probed claims; all
five probe families executed read-only against the SAME trees as HANDOFF.md.

## Trees used
- Stage worktree /home/nea/ensemble-src-wt-stage-v0300, HEAD bb4daa596
  (= origin/latest ee2b46d3b minus ONE tester-evidence doc commit;
  cited-path diff ee2b46d3b..bb4daa596 EMPTY — verification equivalent).
- Canonical checkout /home/nea/ensemble-src (detached 013895287, v0.27.8-era)
  — used for the v0.27.8 notify staged-verification record + blast probe.
- Tags v0.29.0 / v0.29.7 / v0.29.15 via `git tag -l --format`, `git show`.
- External repo /home/nea/Code/data-center-scripts (read-only log/branch).

## FAIL items
- F1 memoir §5 "The five tools": SIX memoir tools registered —
  MEMOIR_TOOL_NAMES frozenset (daemon/tools/memoir.py:170-178) includes
  memoir_about; also in _tool_registry.py:315/:1924; handler memoir.py:1243.
- F2 live-views §4 `_probe_auth_session`: symbol ABSENT from entire daemon/
  (grep zero hits). Actual seam: `resolve_views_session`
  (daemon/routers/live_views.py:1775) — contract docstring matches the
  described behavior verbatim; cited symbol name wrong.
- F3 memoir §7 "additive migrations, no FK": v1 migration carries 1
  self-referencing FK (`superseded_by TEXT REFERENCES memoir_episodes(id)`);
  v2 carries 5 REFERENCES (grep -c). Repeats the known v0.29.0 FK-correction
  imprecision.
- F4 live-views §4 "Percent-encoded paths refused outright (% rejected in
  the tool's validation)": no '%' rejection exists at EITHER tree (worktree
  bb4daa596 AND v0.26-era merge 09d4c9490); validator DECODES percent-escapes
  first, validates decoded form (services/live_views.py:260-262/:290-293).
  Sentence is a verbatim CHANGELOG [0.26.0] quote whose prose ≠ code.
- F5 HANDOFF line counts "50/50/45": actual wc -l = 54/54/49.
- F6 live-views header/§10 base-lane era: CHANGELOG entry for
  liveview-passcode-1 (merge 09d4c9490) sits under `## [0.22.0] — 2026-10-18`,
  not a v0.26-era section; Phase-3 tool surface IS under [0.26.0].

## Disclosures (not reds)
- "6-char alnum" = 31-symbol unambiguous set dropping 0/O/1/i/l
  (models.py:459-469) — true-but-under-specified (also a CHANGELOG quote).
- "rewritten 2026-11-09" = runbook §1.1's own dating; code commit 6398a29a3
  is 2026-11-08 23:24Z; v0.29.7 tagged 2026-11-09 03:30Z.
- Memoir guideline has 11 sections (extra §6 Briefing zones); the 10 mandated
  sections remain in template ORDER (template forbids deletion, not addition).
- Notify commission quote wording ≠ tool docstring's quote (same 2026-10-24
  date; quote provenance is chat, not repo).
- Ambient harness RAM-guard lines (ram_breach_observed) — commands exit 0.

## Verified (major ledger)
Tags/merges/migrations exact (v0.29.0 decree 2026-11-06; v0.29.15 memoir-v2
subject; v0.29.7 bypass-closure subject; 035f26ea1 / 09d4c9490 / 97652f337;
4 migration filenames). All reflection defaults exact (5/1000/4/2/25/30/60.0/
45.0/30/60.0/960.0/3600.0/2d); poignancy 0/20/10; class lines
:4239/:4538/:4704/:4796/:3340. Memoir knob truth table + canonical spellings
+ call-time + one-WARNING-per-boot; ENV-ONLY ×4 sections + yaml-silently-
ignored; keepsakes 12 pins / 4000 tokens / DD-3 at :350,559,900,1421,1430;
24000/48000/1000; 15-min stale takeover; quiet hours 02:00-04:00 UTC; V2-24;
derived-per-tick; embedding parks; inner_soul notice; anomaly 33028b27;
19-refusal-codes D4.8; privileged ordinals (memoir #19/19→#19/21;
notify-user #16; prompt-reload #20; prompt-assent #21); VOCABULARY 6→9;
ari-only allow + body agent gates both lanes. Notify: all 5 defaults +
validators; sha256 target-hash; commit-before-count; accepted-class-only
budget; outermost N9 gate; 10s W4 budget + single retry; Discord 15s;
internal_report/scheduler wake shapes; 1800s floor; v0.27.8 record S1 8/8 /
S2 7/7 / S3 8/8 / S4 PASS-WITH-NOTES (canonical RESULTS file). Passcode:
Fernet+SYSTEM_ENCRYPTION_KEY; UNIQUE pair; ip_hmac; 10-strike durable
lockout; board_link(refresh=True); grant cookie Max-Age 86400 +
HttpOnly/Secure/SameSite=Lax; ViewPasscodeRotationRequired; 32 MiB cap;
rightmost-XFF; 429-no-strike / wrong-code-both; Sec-Fetch-Site; Origin-null
allow; CSP default-src 'none'; no HSTS (grep zero); UUID4; timeout 5m +
before-catch-all; data-center-scripts branch + commit e198005816… verified
in that repo. CHANGELOG: [0.28.17] memoir entry ✓; NO v0.29.7 section ✓;
NO [0.26.2]/[0.26.3] headings ✓ (corroborates UNVERIFIED marks); no
ENSEMBLE-* ids in memoir/notify planning ✓; no notify runbook ✓
(post-restart-arm-notify is a different lane); no ".cc" note ✓.

## Shape probes
wc -l: 54/54/49 — within 40–90 ✓ (but ≠ HANDOFF's 50/50/45 → F5).
Live-views + notify: exactly the 10 template sections in order ✓.
Memoir: 10 in order + 1 extra (D3). README: 3 options + trade-offs +
proposal + lifecycle + 6-item checklist ✓. TEMPLATE: 10 sections ✓.
HANDOFF: all 7 ## headers in order ✓.

## Blast probes
Canonical porcelain = documented baseline EXACTLY: staged 6
(2A scripts/audit/borrow_census.py + scripts/upgrade/journal_read.py;
4M scripts/upgrade/{lib,promote,stage,status}.sh) + 15 unstaged M — no new
entries. Stage-v0300 worktree porcelain-clean ✓. All probe commands
read-only; no writes outside /home/nea/Code/guidelines-drafts/.
