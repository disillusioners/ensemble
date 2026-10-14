# Persona-Memory Sweep — 2026-10-14 (User Directive)

## Provenance

- **Commission:** `feature/agent-prompt-immutability` (branch)
- **Date archived:** 2026-10-14
- **Origin (binding):** User Directive 2026-10-14 — *migration policy reversal*.
  Verbatim intent: **DISCARD existing persona/memory customizations — do NOT
  migrate them into the new DB version store; persistence starts EMPTY at
  go-live (intended first-time-persistence semantic, NOT data loss).**
- **Worker:** coder (overnight autonomous, conservative call + document +
  continue). Verified input inventory at task start.

This archive exists so the migration policy reversal is auditable and
reversible in-place, NOT to preserve runtime content for re-import — see
[§ Reversibility](#reversibility) for the precise split.

## Rulings (R1–R6, binding from user 2026-10-14)

| ID  | Ruling                                                                                          | Status |
|-----|-------------------------------------------------------------------------------------------------|--------|
| R1  | **F1 STANDS** — template file itself = baseline initial version (no DB row); `D-SEED-EDGE` RESOLVED-DEAD: no seeding, no edges, empty DB at go-live.   | binding |
| R2  | Sweep **INCLUDES** live memories surface: tracked `agents/{name}/memory.md` files get ARCHIVED (never deleted) into `legacy/` per repo convention — committed repo-side sweep on this branch. The memories MECHANISM stays (`access_memory` + future file writes continue); only accumulated pre-directive content goes.   | binding |
| R3  | `.agents/{id}/memories` (zero code refs) — archive whatever is tracked.                                                            | binding |
| R4  | RAG — **NO KB purge**. Document the no-consume boundary: the new persona paths must never query/consume persona-class RAG entries (the rewrite already doesn't — verify + document for morning review).                                                            | binding |
| R5  | Repo-committed `agents/*.md` AS-SHIPPED = baseline by fiat. No "pristine reset" chase.                                             | binding |
| R6  | `D-ARTIFACT-DISPOSITION` (memory.md file-vs-DB mechanics) morning fork UNCHANGED — do not expand into it.                            | binding |

## Inventory (verified at task start, before any moves)

| Source class | Pattern                      | Count | Legacy destination                                                                                  |
|--------------|------------------------------|-------|-----------------------------------------------------------------------------------------------------|
| A (memory.md)  | `agents/<name>/memory.md`                    | 11 | `legacy/agent-prompt-immutability-sweep/2026-10-14-user-directive/agents/<name>/memory.md` |
| B (memories/.agents) | `.agents/<id>/memories/*.md`   | 177 | `legacy/agent-prompt-immutability-sweep/2026-10-14-user-directive/.agents/<id>/memories/<file>.md` |
| C (agents/{name}/memories) | `agents/<name>/memories/*.md` | 0   | N/A — invariant `ZERO tracked content exists (verified)`. New release trees start clean by construction; runtime-only accumulations on live installs are out of this branch's scope and handled by R2's mechanism-preservation clause. |
| **Total archived** |                                  | **188** |                                                                                                     |

### A — `agents/<name>/memory.md` (11 files)

`_baby_template`, `_mother`, `architect`, `developer`, `developer[v2]`,
`giter`, `leader`, `maintenancer`, `reviewer`, `reviewer[v2]`, `tester`.

### B — `.agents/{id}/memories/*.md` (177 files, by directory)

| `id`        | files |
|-------------|-------|
| reviewer    | 82    |
| developer   | 82    |
| tester      | 5     |
| charter     | 4     |
| leader      | 2     |
| tidier      | 1     |
| coder       | 1     |
| **Total**   | **177** |

### C — `agents/{name}/memories/` — ZERO tracked content

Verified pre-sweep via `git ls-files agents/*/memories/*.md | wc -l` →
`0`. New release trees start clean by construction; runtime-only
accumulations on live installs are out of this branch's scope. The
mechanism (the directory exists at runtime as carved-out per
`D-ARTIFACT-DISPOSITION`) is preserved unchanged per R2/R6.

## Mechanism status (R2/R6 — preserved unchanged)

The mechanism continues to function: `agents/{name}/memories/` exists
as an unversioned runtime dir per `D-ARTIFACT-DISPOSITION` carve-out
(see `phaseB-plan.md` D7), `access_memory` (`daemon/tools/access_memory.py:32-96`)
reads from it, `inner_soul` writes via `_update_memories`
(`daemon/tools/inner_soul.py:1408`), and `daemon/loader.py:559-580`
`load_recent_memories` lists filenames only into prompt §9. **Only
pre-2026-10-14 accumulated content is archived**; new writes are not
affected.

## RAG no-consume boundary (R4 — verified, documented)

Verified static-grep on `daemon/`:

- `daemon/repositories/prompt_version/` queries `prompt_versions` +
  `prompt_audit_events` + `prompt_selections` tables ONLY. No
  `rag_*` / `experience` / `kb_*` reads on the persona path.
- `daemon/services/prompt_version_service.py` and
  `daemon/tools/inner_soul.py` likewise touch no RAG surface from the
  persona write side.
- The `inner_soul` classification layer (`daemon/tools/inner_soul.py:230-276`
  `_should_redirect_to_rag`) is non-project only and explicitly
  CARVES OUT persona targets (`memory`, `memories`); it is NOT the
  rewrite-side read path.

Conclusion: the new persona paths (`create_and_submit`, `get_effective`)
never query/consume persona-class RAG entries. The rewrite preserves
the "persona ≠ project" boundary by construction — no RAG KB purge
required.

## Reversibility

This is an **archive, not a delete** (R2). The pre-sweep state is
preserved with full git history (`git log --follow` works per moved
file). Two recovery paths, listed weakest→strongest:

1. **One-by-one restore (most likely use).** An agent's archived
   `memory.md` / `memories/*.md` can be inspected with `git log
   --follow` for lineage, and selectively cherry-picked to a future
   agent if/when that agent's runtime legitimately needs the
   historical context — the per-agent decision lives where the
   person working with that agent lives.
2. **Full revert (recovery use).** Reverting this single commit
   restores all 188 files in their original paths (`git revert <sha>`).
   The mechanism that READS those files has not changed; post-revert
   the runtime behavior matches the pre-2026-10-14 state byte-for-byte.

NOT in scope: re-importing archived content into the new DB version
store. Doing so would directly contradict the user directive
(persistence starts EMPTY at go-live, NOT data loss).

## Misc

- Sweep area name choice: `legacy/agent-prompt-immutability-sweep/2026-10-14-user-directive/`
  — parallels `legacy/.agents/shared/planning/opencode-native-tools/`
  (commission-keyed subtrees under `legacy/`) and avoids polluting
  `legacy/agents/` (which holds retired-agent bundles with their full
  `{soul,rule,workflow,…}` set, an unrelated category).
- Stray dirty files observed at task start but **NOT** swept: `.agents/approver/active.md` (modified),
  `.agents/approver/agent-prompt-immutability-tracking.md` (untracked),
  `.agents/shared/planning/designer-agent/install-audit.jsonl` (modified),
  `.smoke/test-fence.sh` (untracked). These are out of sweep scope per
  the "no strays" directive binding; they remain owned by their
  respective workers.
