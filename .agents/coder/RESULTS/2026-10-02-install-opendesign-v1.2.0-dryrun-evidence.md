# install-opendesign v1.2.0 — BYOK dry-run evidence

Date: 2026-10-02T04:54–04:57Z (UTC)
Commit: 9365d61ddb52f95d60a66efec059e7de0f59407b (branch
`feature/opendesign-e2e-contract-skill-e2e`)
Dry-run script: `/tmp/od_dryrun.py` (throwaway)
Worktree: `/home/nea/ensemble-src-wt-skill-e2e`

## Scope

Prove the v1.2.0 BYOK provisioning mechanism end-to-end on an isolated
SQLite file (no live DB write), then revert completely. Exercises the
same code paths the skill's install procedure calls (KMS-Lite mint,
KMS-Lite attach, mcp_server repository update) without touching the
live ensemble daemon or its DB.

## Stages exercised

1. **Schema bootstrap** — `SQLModel.metadata.create_all` on isolated
   SQLite → 42 tables created (mcp_servers included).
2. **Seed** — Inserted one `opendesign` row via
   `OpenDesignMCP().build_config({})` → defaults:
   `config.env = {"OD_DAEMON_URL": "http://127.0.0.1:7456"}` only.
3. **configure-builtin-equivalent** — wrote
   `BYOK_BASE_URL=https://example.invalid/v1` + `BYOK_MODEL=dryrun-model-001`
   plaintext to `config.env` via the same read-row-fresh + commit
   pattern the daemon router uses (`build_config` →
   `update_mcp_server(config=…)`).
   - `BYOK_API_KEY` deliberately omitted from this step (R1 invariant:
     secret-bearing field rides the KMS marker seam, not
     `configure-builtin`).
4. **kms_request** — `kms_lite.kms_request(service="opendesign-dryrun",
   reason="DRYRUN: byok_api_key placeholder (no live secret).",
   actor="install-opendesign-v1.2.0-dryrun")` →
   `{"handle": "KMS_HANDLE_<uuid>", "fingerprint": "<sha256[:16]>"}`.
   - Return value carries `{handle, fingerprint}` only — asserted via
     `set(handle_record.keys()) == {"handle", "fingerprint"}` and a
     cross-check that the resolved plaintext is NOT in the return
     string (handles-not-secrets invariant, P3-WP9).
5. **kms_attach-equivalent** — wrote
   `__KMS_REF__<handle>__` to `config.env.BYOK_API_KEY` + appended a
   binding to `instance_metadata.bound_handles`. Read-row-fresh +
   write pattern matches `daemon/tools/infra.py:1006-1051` (R1: never
   cache+rewrite).
6. **Resolve round-trip** —
   - `re.match(KMS_MARKER_RE, stored_marker)` matches (the same regex
     `daemon/services/kms_resolver.py` uses).
   - `kms_resolve_handle(handle)` returns the original plaintext
     (43 chars, redacted in log).
   - `json.dumps(row.config)` does NOT contain the plaintext (R1
     invariant: plaintext only leaves the encrypted store via
     `kms_resolve_handle`, never in the stored row).
   - `kms_fingerprint(handle)` round-trips the value.
7. **REVERT (the fence-respecting part)** —
   - Wrote defaults back to `config` (mirrors `/reset-builtin`
     behaviour).
   - Cleared `instance_metadata.bound_handles` (explicit clean-revert
     step; production `reset-builtin` preserves it as history).
   - `kms_lite.reset_store_for_tests()` — drops the process-global
     `_store` singleton and drains the plaintext registry.
   - `kms_resolve_handle(extracted_handle)` returns `None` post-revert
     (handle is dead without the store).
   - Disposed the SQLAlchemy engine and `unlink()`ed the SQLite file
     + `rmdir()`ed the work dir — no on-disk trace remains.

## Evidence — exact outputs

```
[setup] work dir: /tmp/od_dryrun_enbhp0f5
[setup] sqlite:   /tmp/od_dryrun_enbhp0f5/instances.db
[setup] fernet key: present (length=44 bytes)
[seed] opendesign row id=802c485a… defaults: env.OD_DAEMON_URL only
[kms] marker format: prefix='__KMS_REF__' handle_prefix='KMS_HANDLE_' suffix='__'
[kms] marker regex: ^__KMS_REF__(KMS_HANDLE_[A-Za-z0-9_-]+)__$
[pre]  row.config.env keys: ['OD_DAEMON_URL']
[conf] row.config.env after configure: ['BYOK_BASE_URL', 'BYOK_MODEL', 'OD_DAEMON_URL'] (byok_api_key absent: True)
[mint] kms_request → {'handle': 'KMS_HANDLE_e4ea7fd4be774a768d20d8cc9b7d1972', 'fingerprint': 'bb4f5118b7d772be'}
[mint] fingerprint: bb4f5118b7d772be
[mint] kms_request return carries {handle, fingerprint} only — plaintext NOT in return ✓
[mk]   marker: __KMS_REF__KMS_HANDLE_e4ea7fd4be774a768d20d8cc9b7d1972__
[mk]   marker matches KMS_MARKER_RE ✓
[att]  row.config.env after attach: ['BYOK_API_KEY', 'BYOK_BASE_URL', 'BYOK_MODEL', 'OD_DAEMON_URL']
[att]  BYOK_API_KEY stored as marker (NOT plaintext): __KMS_REF__KMS_HANDLE_e4ea7fd4be774a768d…
[att]  instance_metadata.bound_handles entries: 1
[res]  extracted handle from stored marker: KMS_HANDLE_e4ea7fd4be774a768d20d8cc9b7d1972
[res]  marker → handle → plaintext resolves (length=43 chars, redacted in log: zMQ***[43]***)
[res]  plaintext is NOT in stored config ✓ (R1 enforced)
[res]  fingerprint round-trip ✓ (bb4f5118b7d772be)

[rev]  === REVERT ===
[rev]  row.config.env after reset: ['OD_DAEMON_URL'] (BYOK_* absent: True)
[rev]  KMS store singleton dropped; plaintext registry drained
[rev]  post-revert resolve on KMS_HANDLE_e4ea7fd4be774… → None ✓
[rev]  isolated work dir removed: True
```

## Live row sanity check (pre and post dry-run)

```bash
$ curl -s http://127.0.0.1:9797/api/mcp-servers | jq '.mcp_servers[] | select(.name=="opendesign") | {env_keys: (.config.env | keys), bound_handles: (.instance_metadata.bound_handles // [] | length)}'
{
  "env_keys": ["OD_DAEMON_URL"],
  "bound_handles": 0
}
```

→ Unchanged before, during, and after the dry-run. Fence
("LIVE = READ-ONLY: ~/agents-ensemble install, port 9797,
ensemble_prod DB") honoured.

## Note on dry-run noise

`kms_request` calls `append_install_audit(EVENT_KMS_ISSUE)` per mint
(`daemon/services/kms_lite.py:442-450`). The dry-run produced 3
audit entries (one per rerun). These were reverted in
`.agents/shared/planning/designer-agent/install-audit.jsonl` (76 → 73
lines net dry-run delta is zero) before the skill commit; the live
audit trail is unchanged.