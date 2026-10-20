# LESSONS — v0.26.0 staged verification (2026-10-20) — reusable gotchas

Six gotchas banked by the 6-lane independent gate of `/home/nea/agents-ensemble/releases/v0.26.0`
(staged tree `14a7f3762`, detached worktree `/home/nea/ensemble-src-wt-ari-tools`).
Full evidence: `RESULTS/2026-10-20-v0260-staged-verification.md`.

---

## L1 — "grep the binary for the table names" is NOT mutation-grade (PyInstaller compression)

**Trap.** The obvious rollback-safety probe —
`strings releases/v0.25.7/ensemble-prod | grep -c -E "board_items|board_notes|board_item_tags"` —
returns **0 on the OLD binary (as desired) AND 0 on the NEW binary (wrong)**. PyInstaller compresses
python bytecode, so *table-name literals never surface as raw strings* in the ELF payload. The
prescribed positive control therefore fails and a real "the new binary contains board code" claim
would be unfalsifiable.

**Working probe (discriminating).** Use **module-path identifiers** — they survive as path strings:
`daemon.models.board`, `daemon.services.board_service`, `daemon.services.board_view_service`,
`daemon.tools.board_tools`, `daemon.tools.board_link`.
→ OLD: `0/0/0/0/0` · NEW: `1/1/1/1/1`. Pair it with `git grep -l -E "<table names>" <tag> -- daemon/`
(OLD → 0 files, NEW → 6 files) — both legs carry a working control.

**Generalisation.** Any "does the old artifact mention X" claim must be re-proved with a probe that
**does fire on the new artifact**. If the control does not fire, the probe is worthless — repair the
probe, not the claim.

---

## L2 — the prompt-artifact baseline is TREE-ROOT-relative; `cd agents/ && sha256sum -c` is CWD-unsafe

**Trap.** The obvious seal check — `cd <wt>/agents && sha256sum -c .prompt-artifacts.sha256` — yields
**161 spurious "FAILED open or read"** because the baseline keys are `agents/...`-prefixed
(tree-root-relative), so running from inside `agents/` resolves nothing. A naive reading is a
catastrophic false FAIL (161 red) on a perfectly good payload.

**Authority.** `scripts/protection/generate_prompt_artifact_baseline.py --check` (read-only mode; its
docstring pins "run from the tree root") →
`PROMPT-BASELINE: OK — 161 versioned artifact(s) match .prompt-artifacts.sha256`.
Corroborate with a raw `sha256sum -c` executed **from the tree root** → 161 OK / 0 FAILED.
Note the seal set covers only canonical prompt homes (4–5 `.md` per agent) — a file like
`agents/developer/skills-template/dev-strategy.md` is legitimately outside it.

---

## L3 — manifest fields vs shared-meta-KV fields: not everything in the composition record is on disk

`releases/<ver>/manifest.json` carries: `version`, `binary_version`, `staged_at`, `known_schema_gen`,
`contains_contract_phase`, `rollback_safe`, `launcher_sha256`, `binary_sha256`, `config_sha256`,
`agents_tree_sha256`, `frontend_tree_sha256`, `plugins_tree_sha256` + the 3 per-file maps.
**Not present:** `staged_version`, `fe_index_sha256`, `migration_delta_vs_prev` — those live only in
the shared meta-KV staging record. `fe_index` is *derived* (sha256 of
`frontend/dist/frontend/browser/index.html`), not a manifest field. Don't treat a meta-KV value as a
disk-assertable one (or vice versa).

---

## L4 — "no destructive DDL" is a claim about the APPLIED (UP) section, not the file

A new migration file may legitimately contain `DROP TABLE` / `DROP INDEX` **after** the `-- DOWN`
marker — the runner applies only the UP section
(`up_match = re.search(r"--\s*UP\s*\n(.*?)(?=--\s*DOWN|$)")`), and the DOWN block is the documented
manual rollback path, never auto-executed. Prove it with the **real parser**, not by reading:
UP statements = 9 · UP contains DROP = False · UP contains ALTER = False ·
DOWN statements = 9 · DOWN contains DROP = True · `MANUAL:TRUE` present = False.
State the claim precisely: *"no destructive DDL in the applied path"*.

---

## L5 — on PostgreSQL the board tables are created by the ORM `create_all`, not by the `.sql`

Booting the staged binary against a **fresh scratch PG** creates `board_items` / `board_notes` /
`board_item_tags` (and all other tables) through the ORM path. Consequences for verification:
- `schema_migrations` = **0 rows** on a fresh PG DB is **expected**, not a missing-schema defect
  (the runner short-circuits: `if "sqlite" not in str(self.engine.url): return []`).
- The release payload ships **0 `.sql` files** (source-less payload) — the `.sql` file is the
  SQLite/audit record, not the PG mechanism.
- Therefore "additive-only" is doubly true on PG: the old binary has no board models (so
  `create_all` cannot touch `board_*`) **and** the new `.sql` never executes there at all.

---

## L6 — tip drift after staging: the L1 guard will REFUSE a re-stage

A staged artifact is tip-bound **at stage time** only. When `origin/latest` advances after the stage,
the payload stays internally consistent (provenance chain, sidecars, tag, manifest all still verify),
but:
- `git ls-remote origin refs/heads/latest` ≠ the staged commit ⇒ the numbers of record go stale;
- a re-stage from the old staging worktree is **refused** by the L1 tip-identity guard
  (`HEAD ≠ origin/latest` ⇒ exit **78** `non-tip-tree`);
- a re-stage needs a **fresh tip worktree + rebuild** — the L2 provenance sidecar binds `git_head`,
  so even a tests-only commit changes the binary hash (payload trees may stay provably identical);
- a newer **local-only tag** can exist while `releases/` has no directory for it ⇒ the *staged*
  artifact and the *newest tag* are different things. Always report which one a promote would
  install.

---

## Cross-lane hygiene note

When several verification lanes share one staging worktree, "a concurrent foreign lane is writing
here" is usually **your own sibling lane**. Attribute before escalating: pytest `__pycache__` mtimes
identify the lane, and a `data/logs/ensemble.log` mtime bump with no daemon started is the
module-level `create_app()` import side-effect of a suite run (gitignored, tracked tree unaffected).
Track the tracked tree (`git status --porcelain` / `git diff --quiet HEAD`) and the untracked
byproducts separately — the tracked tree is what the release claim depends on.
