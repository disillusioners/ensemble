# P3 — Proof (b): Live Agent-Turn §5 Cycle (disposition A GO) — Evidence

**Verdict: GREEN (steps 1-6c) with two honestly-documented variances + one out-of-fence finding (PB-F1, step 6d) + one residual wiring gap (requirement_json backfill).**
- Date: 2026-09-27 (cycle window 05:04-05:20Z; gateway real, disposition A)
- Operator: developer[v2] phase-lead; launchpad :8081 (DB ensemble_designer_p3, code @ a2a9739e incl. in-proof fix)
- Runbook: verdicts/p3-wp12-e2e-rollout.md §5 (verbatim execution; transport adaptations noted per step)

## 0. Pre-registered framing decisions (documented BEFORE each action)
1. Runbook step-2 premise (capability_missing) requires a row-absent state; opendesign is a BUILTIN row (delete 403-protected) — the reachable precondition is row-present-but-inactive, which the implemented tri-state maps to kind=installed_but_unconfigured (a day-1-valid firing kind). Both branches captured.
2. Precondition restoration (PUT is_active=false) = state restoration to the runbook's tested initial state (the deterministic e2e also ran from a no-live-row state), documented with timestamps; net-zero end state (installer reactivated).

## 1. Assertion table
| Step | Runbook assertion | Live result | Verdict |
|---|---|---|---|
| 1 | Spawn worker w/ opendesign-verify task | job 0cf9b785 → instance 4683204f; skill injected (bank selection); real LLM turns via real gateway (worker resolves coding/coding2 → glm backend) | GREEN |
| 2 | Miss envelope `Result:` JSON | `Result: {"kind":"installed_but_unconfigured","capability":"opendesign","installer_skill":"install-opendesign","detection_evidence":"pre_flight: mcp_servers.query(name=opendesign) -> row present, is_active=False","blocker_scope":"this_task","resume_hint":"step_after_install","policy_denied_reason":null,"ts":"2026-09-27T05:12:50Z"}` — schema-exact (7 fields + ts, extra=forbid-validated), produced by a REAL agent turn executing the REAL resolver code (capability_check + format_envelope_message) against the live PG row. Kind variance per framing decision 1. | GREEN w/ note |
| 3 | Installer dispatch; idempotent replay | job b294037d → instance bcdaf305 (real install-opendesign skill); configure-builtin idempotent replay REACTIVATED row (is_active false→true, HTTP 201, idempotency key adde84cf…, second replay = unchanged updated_at); test-connection 200 / 10 od_* tools; zero-credential env (OD_DAEMON_URL only); bound_handles=None | GREEN w/ note (replay = no-write by design → no NEW audit line this step; session audit lane already carries mcp_install + kms_issue lines from the WP12 gate window, timestamp-distinguishable) |
| 4 | Resume via job_continue w/ [resume] body | HTTP lane has no job_continue route → messages-revive to the COMPLETED worker instance (checkpoint-reuse semantics identical; transport adaptation documented); [resume] 4-field body delivered (capability_id, status, tools_now_available ×10, resume_from) | GREEN w/ note |
| 5 | Verification report: present + od_* surface | Worker validated [resume] block (not malformed → no escalation), RE-RAN capability_check fresh (real code, live row → present; never trusted carried status), corroborated installer via daemon logs (05:14:31 reactivation; 05:14:38 test-connection 200), delivered the exact 10-tool od_* surface | GREEN |
| 6a | Stored config marker-only | `{"OD_DAEMON_URL": "http://127.0.0.1:7456"}` — no plaintext secret, no marker (zero-credential posture) | GREEN |
| 6b | No mint: bound_handles empty + no kms_issue | bound_handles NULL; 0 audit lines in proof window [05:04→]; the 22:33Z Sep-26 kms_issue lines = WP12 boot-gate evidence session (pre-proof) | GREEN |
| 6c | Log scan clean (current boot window) | 0 KMS_HANDLE refs, 0 key-shaped strings post-05:04:23 boot | GREEN |
| 6d | Plaintext-absence in messages/checkpoints | KMS-scope clean (no KMS secret exists in run); **PB-F1 (below)** — ambient embedding key captured in ONE tool result | NOT-GREEN (out-of-fence finding) |

## 2. Control runs (bonus evidence)
- Run 1 (job 8f43441d / worker 330d7276, pre-fix, row ACTIVE): present-branch verification incl. a REAL live MCP tool call mcp_opendesign_od_list_projects → honest "OD daemon unreachable: fetch failed" per the skill's day-1 host note — real MCP spawn + routing + failure surface.
- Run 3 (job 8f6d5eb8 / worker 17dce5f8, post-fix, row ACTIVE): present-control — gate correctly NOT firing on present.

## 3. In-proof fix (iteration 1/3, GO-pre-authorized fence)
- Root cause (run 2, worker 498ca5b2, verbatim): "the skill's first instruction is to run capability_check(...) — Since I don't have that as a direct callable, the equivalent evidence is my own tool inventory" → agent invented kind=capability_present (non-schema). The injection-time capability gate (WP2 spec) was never wired into the live injection path.
- Fix a2a9739e: requirement_json persisted on SkillBankItem (+migration, PG mirror); _capability_preflight_block wired into inject_skills + inject_explicit_skill; manager wiring _wire_skill_injection_capability_gate; configure-builtin replay reactivates inactive rows. 14 new tests, 315 targeted green, byte-identical back-compat pin.
- **Residual gap (fix 2/3 candidate, NOT blocking):** the seeded skill rows predate the fix; re-seed did not backfill requirement_json (version-match no-op) → the runtime-prepended pre-flight block did NOT fire live this run. The step-2 envelope was instead produced by the agent EXECUTING the real resolver itself (stronger agent behavior, weaker runtime evidence). Backfill = seed-path refresh of requirement_json on unchanged-version rows (or one-shot backfill) + reboot.

## 4. PB-F1 — ambient-env key capture via bash tool result (OUT of P3 KMS fence; pre-existing class)
- Live demo: worker 4683204f message [17] tool_calls result carries `BLUEPRINT_EMBEDDING_API_KEY` / `EMBEDDING_API_KEY` / `SKILL_EVOLUTION_EMBEDDING_API_KEY` = sk-proj-[REDACTED-EMBEDDING-KEY] (3 occurrences) — the worker's compound bash command ended with an env dump; the bash tool inherits the daemon's FULL process env (worktree .env exports the embedding keys), so the raw key transited tool result → message → checkpoint (p3 DB, instance 4683204f).
- The worker self-noticed ("process env contains live API keys — I will not echo those in any report") and redacted subsequent commands; the first dump persisted.
- Scope: NOT a KMS-minted secret (KMS proofs 6a-6c green); the WP10 redaction filter covers KMS-registered plaintexts only (day-1 design) — ambient env keys pass.
- Containment: evidence copy redacted (verified zero residual). Checkpoint copy sits in retained ensemble_designer_p3 — CALLER DECISION: shred that message row at teardown vs accept (key legitimately lives in gitignored .env on the same host).
- Follow-up commission class (either): (a) bash-tool env hygiene (allowlist/scrub key-shaped exports at tool spawn); (b) extend WP10-style shape-scrub to key-shaped strings at the tool-result boundary.

## 5. Evidence files (ops lane, /tmp/p3boot/proof-b/, pre-redacted)
step1-job-create.json, job-final.json ×4 runs, worker{1,2,4}-messages.json (redacted), run1-final-report.txt, run4-final-report.txt, installer-final-report.txt, run4-resumed-final.txt, delete-row.json, deactivate{,2}.json, step6-scans.sh + outputs, resume-response.json.

## 6. Disposition
- :8081 daemon released gracefully post-evidence (per GO lane discipline; DB ensemble_designer_p3 + /tmp/p3boot retained; gateway :4124 UP, shared, never torn down).
- Proof (b) = the last pinned slice; BOTH proofs now green (proof (a) per caller 04:37Z) → merge protocol may fire (giter re-fetches upstream first).
**Containment COMPLETED (caller Decision 1 — SHRED): 2026-09-27T05:26:27Z — instance 4683204f thread fully deleted from ensemble_designer_p3 (380 checkpoint_writes + 98 checkpoint_blobs + 145 checkpoints + 2 message_queue + 2 message_metadata rows); DB-wide residual scan ZERO across checkpoint_blobs/checkpoint_writes/checkpoints/message_queue/message_metadata/event/instances/job_queue_items.message/task.result/task.error (bytea-native position() matching + text LIKE, counts only, values never echoed). Branch-priority landing before merge window.**
