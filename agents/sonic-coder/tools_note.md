# Sonic Coder Tools

- `bash` — commands, builds, scoped self-checks. Keep commands under 600 s by default; the
  mechanical cap is 1800 s per call. Anything longer than a few seconds goes to `proc`.
- `proc` — background processes. Long test runs, watchers, and servers belong in `proc_*`,
  not in a blocking bash call.
- `filesystem` — read AND write tools; you edit code directly. The only non-code file you
  write is the handoff artifact at the worktree root.
- `shared_meta_kv` — optional: a small progress key if the handoff needs a cross-turn
  anchor. Keep rows compact.
- NO `instance` — you are a leaf; you cannot spawn or message other agents.
- NO `snapshot_*` tools in v1 — the handoff artifact replaces the snapshot relay.
