# wi-1fbee2ec — S1 (ARTIFACT INTEGRITY) + S4 (CEREMONY INPUTS) — v0.28.5 staged verification
# Verdict: ALL 10 STEPS PASS — ceremony-ready. Read-only discipline held (worktree porcelain empty before/after; live fence untouched).

4-WAY (all == giter-reported a59e09a40958d267fb2942722830ff77673b84d38d212dfb28291f5c2f52a369):
  (a) /home/nea/agents-ensemble/releases/v0.28.5/ensemble-prod           = a59e09a4…a369
  (b) /home/nea/ensemble-src-wt-stage-v0285/dist/ensemble-prod           = a59e09a4…a369
  (c) manifest.json binary_sha256                                        = a59e09a4…a369
  (d) dist/ensemble-prod.build-provenance.json artifact_sha256           = a59e09a4…a369
  FE sidecar: frontend/dist/frontend/browser/index.html.build-provenance.json artifact_sha256=5531cbe4…, git_head=600057a83bacf494e97e473b19bd2e1a0d2fe2f4, git_dirty=false
  BOTH sidecars git_head=600057a83bacf494e97e473b19bd2e1a0d2fe2f4 / git_dirty=false

TAG: local annotated object 58ba56ef994f828e83a891e6a4398b01c4002d42 (cat-file -t = tag) -> peel 600057a83bacf494e97e473b19bd2e1a0d2fe2f4
     remote ls-remote: annotated 58ba56ef9… + peeled ^{} 600057a83… (PUSHED, verified server-side)
TIP: HEAD == origin/latest == v0.28.5^{commit} == 600057a83bacf494e97e473b19bd2e1a0d2fe2f4 (HEAD subject "chore(release): bump version to 0.28.5") — ZERO TIP AMBIGUITY

MANIFEST: agents 338 / frontend 93 / plugins 4930, total 5361; rollback_safe=True; known_schema_gen=20261031_120002_create_project_skill_usage_records.sql;
  pipeline_scripts_floor_commit=953603108fa35d4f59031818c3091eecda8c1663; fe_index not top-level (None) — carried by frontend_manifest['dist/frontend/browser/index.html']=5531cbe4… == FE sidecar sha

CENSUS (staged tree): 119 *.sql, 119 distinct YYYYMMDD_HHMMSS prefixes, uniq -d EMPTY. Newest = 20261031_120002 (zero-DDL lane consistent).

PAYLOAD: release dir contains ONLY agents/ frontend/ plugins/ ensemble-prod launcher.sh config.yaml manifest.json — no scripts/, no .env, no provenance sidecars, no .staging.*/.aside.* residue.

PORCELAIN: staging worktree status --porcelain EMPTY (verified twice: start + post-baseline-check), HEAD detached @ 600057a83 (tag v0.28.5, origin/latest decorations).

PROMPT-BASELINE: cd staging-wt && env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT -u DATABASE_URL -u PORT /home/nea/ensemble-src/.venv/bin/python scripts/protection/generate_prompt_artifact_baseline.py --check
  -> "PROMPT-BASELINE: OK — 161 versioned artifact(s) match .prompt-artifacts.sha256"

CEREMONY COMMAND (verbatim): ENSEMBLE_UPGRADE_LIVE=1 VERSION=v0.28.5 bash scripts/upgrade/promote.sh live --f2-verified-closed
ROLLBACK (tag annotation, verbatim): "ROLLBACK ADJUDICATION (standing rule, source=explicit): migration delta EMPTY — lane zero-DDL (git diff f7039c3ff..40bfc6d43 daemon/migrations/versions/ empty), drift zero-DDL, merged tree vs live v0.28.4 (62627265f) empty; behavior-only payload; report_injections substrate predates; old binary references no new symbols => ENSEMBLE_ROLLBACK_SAFE=1; auto-rollback may fire safely on a failed gate."
  manifest rollback_safe = True — consistent. Payload grep "rollback_safe": manifest.json (sanctioned field) + launcher.sh (house launcher consumes the field; launcher sha a3a44461… unchanged since v0.27.6) — no stray stage-log lines (stage log lives with giter; not-found in payload, not a failure).

LIVE FENCE BASELINE (for S2 cross-check):
  symlink: /home/nea/agents-ensemble/current -> releases/v0.28.4  (NOTE: 'releases/current' does not exist — symlink is at install ROOT)
  state.json path: /home/nea/agents-ensemble/releases/state.json  sha256 PRE = 72be3af75834886614f288d5baff3d38936acbddf931907e4619a97686132273
  promote fields: current=v0.28.4 previous=v0.28.3 in_flight=None pending_op=None quarantined=[]
  livez (read-only): {"status":"alive","uptime_seconds":24756.3,"version":"0.28.4"}
  fence integrity: state.json sha256 IDENTICAL pre/post this lane; porcelain EMPTY post-run.

DEVIATIONS FROM BRIEF (none value-bearing):
  1. FE sidecar NOT beside dist binary — at frontend/dist/frontend/browser/index.html.build-provenance.json (found, intact).
  2. fe_index_sha256 not a top-level manifest scalar (None) — per-file map + sidecar carry it (v0.27.8 precedent).
  3. 'releases/current' literal path in the brief: actual symlink is /home/nea/agents-ensemble/current -> releases/v0.28.4.
  4. state.json at releases/state.json (not install root).
  5. releases/ currently holds v0.28.0/2/3/4/5 — no v0.28.1 dir (superseded staged-unpromoted, pre-existing; not this lane).

Logs: /tmp/wi1f-v0285-s1-20261101/s1.log (+ per-step runner scripts). Run window: 2026-11-01T20:37:07Z–20:40:21Z.
