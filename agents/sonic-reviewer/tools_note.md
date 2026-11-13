# Sonic Reviewer Tools

- `filesystem` READ tools: `read_file`, `grep_files`, `glob_files`, `list_directory`. No
  write tools — `edit_file` and `write_file` are denied.
- `bash` for READ-ONLY inspection: `git status|diff|log|show` (including the FULL MERGED
  DIFF `base..HEAD`), grep/rg, file inspection. NEVER mutate files, NEVER run tests/packs
  (sonic-tester owns execution), NEVER contact the live daemon/DB. This is the one
  prompt-enforced residual — nothing mechanical stops a bash write except this rule.
- DENIED: `proc`, `instance`, `service`, `midflight`, `shared_meta_kv`, `infra`, `mcp`,
  `secrets`.
- NO `knowledge` — you make no RAG/knowledge call; the handoff and the merged diff are
  your entire input.
- `pok` / `nest-report` — ambient reads and anomaly reporting.
