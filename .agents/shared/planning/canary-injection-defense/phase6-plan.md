# Phase 6: Staging Handoff (Giter-Owned)

## Objective

Perform the **release mechanics** — the part that is not lane content but lane mechanics — and hand
the user a **promote-ready, fully-provenanced** artifact:

slot re-derivation → merge `--no-ff` → 4-file version bump → **pushed** annotated tag → detached
stage worktree at the tag → env-first stage (live-dir write only, journaled) → the staged release
with full provenance → **the promote command recorded, not executed**.

**No promote.** The flip is the user's 3-factor nonce ceremony.

**Staged payload additions from the rulings:** the staged payload now includes the committed scanner
`agents/canary/skills-template/injection_scan.py` (an `agents/` file, shipped verbatim); the staged
tests run with the LLM pass **OFF** (deterministic heuristic path); the arithmetic (baseline 181,
agents tree 378, discoverable 44, T29 25 cells) is **re-derived at stage**, never frozen; the
promote command is recorded **NOT run**.

---

## Entry Criteria

- **Phase 5 exited** — gates **A–H** are green on the committed branch.
- The **finalize guard** passes: `origin/latest` is **unmoved** from the lane base
  `97b76e20e1d640c69b720149547e93565ab22b4c`. **If it moved** (the concurrent memoir lane is the live
  candidate) ⇒ **STOP**, re-derive the merge base, re-run the Phase 5 gates on the merged tree, and
  only then continue. The guard is never overridden.
- The **slot** is derived at this moment — **never reserved** (§Risks R5: `v0.28.13` is **contended**
  with the memoir lane).

---

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | **Derive the version slot (W9)** — enumerate the local + remote tags, the `releases/` directory contents and the current `__version__`; pick the **next free slot strictly above all of them**. `v0.28.13` is **contended** with the memoir lane — whichever lane stages first takes it; this lane **re-derives**, it never reserves. | — | The slot is derived from live state at this moment, and the derivation (tags + dirs + current version) is written into the lane record. |
| 2 | **Re-derive the baseline on the merged tree.** If the memoir lane landed `agents/**` edits, the row count may have moved (a content edit changes a hash, not a count; a **new** prompt file adds a row). The re-stamp is computed **on the merged tree**, never on the lane branch. | — | The merged tree's baseline file exists, is clean, and its row count equals the **re-derived** value (181 unless a foreign lane added prompt files — in which case the re-derived number wins and the discrepancy is recorded). |
| 3 | **Merge `--no-ff`** the lane into `origin/latest`. Merge conflicts: `PACKS.md` resolves **keep-both, newest-first** (house precedent). If the memoir lane touched `agents/aegis/**`, keep **both** lanes' content and re-stamp **once** afterwards. | 1, 2 | The merge is `--no-ff` (no fast-forward squash); the merge parents are recorded; no conflict resolution silently drops a lane's content. |
| 4 | **The 4-file version bump** — `pyproject.toml`, `daemon/__init__.py`, `tests/test_api.py`, `uv.lock` — exactly one hit each; **targeted `git add`**, never `add -A`. The house commit message is `chore(release): bump version to <slot>`. `CHANGELOG` is touched only if a live `[Unreleased]` section exists. | 3 | The bump diff is exactly 4 files, 1 line each; the working tree has no stray staged paths. |
| 5 | **Push the annotated tag** — the tag is **pushed** (the house convention), pointing at the bump commit; the annotation carries the rollback adjudication and the **no-promote disclosure**. Push order: `latest` first, then the tag. | 4 | The tag exists locally **and** on the remote and peels to the bump commit. |
| 6 | **Create the detached stage worktree at the tag** — the worktree is **kept** (not removed) so both provenance sidecars remain recoverable for the ceremony (the predecessor's v0.25.2 provenance-gap pattern is the precedent). | 5 | The worktree is detached at the tag, `git status --porcelain` is empty, and both sidecars are present. |
| 7 | **Stage env-first against the live target** — the stage writes the release directory **only** (live-dir write, journaled); `ENSEMBLE_ROLLBACK_SAFE=1` is set **explicitly** with the reason recorded. | 6 | The release directory exists with the binary + manifest + both sidecars; the stage is journaled; **the promote fields of the state journal are untouched**. |
| 8 | **Migration-delta census** — count the `.sql` files in the **staged binary** and in the **live** binary and compare with the source tree. This lane authors **zero** `.sql` files, so its own delta is **EMPTY**. If the memoir lane landed, the delta is **+1 (memoir's migration)** — the adjudication must then reason on the **merged** delta: an additive `CREATE TABLE` is inert on rollback ⇒ `rollback_safe = true`, source explicit, and the reasoning names the migration as **memoir's**, not this lane's. | 7 | The census is stated at all three levels (source / staged binary / live binary); the rollback-safe reason matches the **actual** merged delta. |
| 9 | **4-way sha verification** — release dir == stage-worktree dist == manifest == provenance sidecar. | 7 | All four agree; the manifest's per-file entries re-hash clean (the whole payload). |
| 10 | **Payload hygiene** — the release directory is **source-less** (no `scripts/`, no `.env`, no `daemon/`, no `.git/`, no build residue); the payload's prompt-artifact baseline is clean; no `.staging.*` / `.aside.*` residue; **the staged payload contains `agents/canary/skills-template/injection_scan.py`** (a shipped `agents/` file — its manifest entry re-hashes clean in task 9), and **the scanner leg is executed from the STAGED tree**: the three-CWD byte-identity run (repo root; `agents/`; a consumer cwd) is repeated from the stage worktree detached at the tag — the same tree the payload was built from — so the worktree execution certifies the payload copy through the manifest re-hash (byte-identical ⇒ one artifact). **Executable at stage time**: bare `python3`, no daemon import, no venv, no network — the stage worktree is a plain checkout, so no compensating-evidence escape is needed; the staged three-CWD digests must additionally equal the P5 branch-run digests. | 9 | The payload shape matches the house pattern; the baseline in the payload is clean at the re-derived row count; `injection_scan.py` is present in the payload manifest; the staged three-CWD runs are byte-identical to each other and to the P5 branch-run outputs. |
| 11 | **Live fence: PRE == MID == POST** — the live daemon, the `current` symlink, the live `/livez` version and the promote fields of the state journal are **byte-identical** before, during and after the stage. | 7–10 | Three reads of the fence fields are identical; the live daemon's uptime is continuous across the stage window. |
| 12 | **Record the promote command — DO NOT RUN IT.** The command is written into the lane record with its ceremony preconditions (the arm-time scripts-dir guard and the tip-identity check), together with the **no-promote disclosure**. | 11 | The command is **recorded**; the stage never invoked it; no promote field in the journal was written. |

---

## Gates

| Gate | Criterion |
|------|-----------|
| **G6.1a** | The release-mechanics gate: merge is `--no-ff` with recorded parents; the bump is **exactly 4 files / 1 line each**; the tag is **pushed** and peels to the bump commit; the stage worktree is detached at the tag with a clean status and both sidecars present. |
| **G6.1b** | The artifact gate: 4-way sha match; payload source-less; the baseline clean in the payload; the agents tree re-derived (**378** or the re-derived value); the staged payload contains `agents/canary/skills-template/injection_scan.py` and the **staged three-CWD scanner run** is byte-identical to the P5 branch run. |
| **G6.2** | The **migration-delta census** is stated at all three levels, with the rollback-safe reason matching the **actual merged delta** (EMPTY if no foreign lane landed; +1 memoir's if it did — reasoned, not assumed). |
| **G6.3** | The **live fence** is byte-identical PRE == MID == POST; the live daemon is untouched. |
| **G6.4** | The **promote command is recorded and NOT executed**; no promote field was written. |
| **G6.5** | The slot derivation is recorded (the contention with the memoir lane resolved by live state, not by reservation). |

---

## Verification Commands

```bash
cd /home/nea/ensemble-src-wt-canary-injection-defense

# --- DERIVATION 1: the slot (W9) -- derive at stage time, NEVER reserve ---
git tag --list 'v0.28.*' | sort -V | tail -5
ls -1 /home/nea/agents-ensemble/releases/ | sort -V | tail -5
grep -n '__version__' daemon/__init__.py
#   the slot is the next free one strictly above ALL of these.
#   v0.28.13 is CONTENDED with the memoir lane -> whichever stages first takes it.

# --- DERIVATION 2: the merge shape ---
git log --oneline --graph -3 origin/latest
git show --no-patch --format='%P' HEAD        # two parents => --no-ff (no squash)
git show --stat HEAD~1 | tail -20             # the bump: EXACTLY 4 files, 1 line each

# --- DERIVATION 3: the tag ---
git cat-file -t v0.28.13                       # 'tag' (annotated, not lightweight)
git rev-parse v0.28.13^{commit}               # peels to the bump commit
git ls-remote --tags origin | grep v0.28.13   # present on the remote

# --- DERIVATION 4: the migration-delta census (three levels) ---
ls daemon/migrations/versions/*.sql | wc -l                       # source tree
python3 - <<'PY'
import zipfile, glob, re, sys
#   count the .sql entries embedded in the STAGED binary and compare with the LIVE binary
def census(p):
    z = zipfile.ZipFile(p)
    return len([n for n in z.namelist() if n.endswith('.sql')])
for label, path in (("staged", "<release-dir>/ensemble-prod"), ("live", "<live-install>/ensemble-prod")):
    try: print(label, census(path))
    except Exception as e: print(label, "ERR", e)
PY
#   this lane authors ZERO .sql -> its own delta is EMPTY.
#   if the memoir lane landed: +1 (memoir's) -> reason on the MERGED delta, naming it memoir's.

# --- DERIVATION 5: the 4-way sha ---
sha256sum <release-dir>/ensemble-prod <stage-worktree>/dist/ensemble-prod
python3 -c "import json;print(json.load(open('<release-dir>/manifest.json'))['binary_sha256'])"
grep -o '\"artifact_sha256\"[^,]*' <release-dir>/ensemble-prod.build-provenance.json
#   all four MUST agree.

# --- DERIVATION 6: payload hygiene + the baseline inside the payload ---
ls <release-dir>/                              # ensemble-prod, manifest.json, sidecars -- NO scripts/, NO .env
python3 - <<'PY'
import json
m = json.load(open('<release-dir>/manifest.json'))
print("agents", len(m.get("agents", {})), "frontend", len(m.get("frontend", {})), "plugins", len(m.get("plugins", {})))
print("rollback_safe", m.get("rollback_safe"), "| known_schema_gen", m.get("known_schema_gen"))
PY
#   agents == 378 (or the re-derived value) | frontend/plugins unchanged | rollback_safe True

# --- DERIVATION 7: the live fence PRE == MID == POST ---
readlink /home/nea/agents-ensemble/releases/current
sha256sum /home/nea/agents-ensemble/state.json
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:9797/livez     # 200 + the live version
#   re-read all three AFTER the stage -> byte-identical, uptime continuous.

# --- DERIVATION 8: the promote command is RECORDED, NOT RUN ---
grep -n 'ENSEMBLE_UPGRADE_LIVE=1' .agents/shared/planning/canary-injection-defense/*.md
#   the command text appears in the RECORD; no promote field in state.json was written.

# --- DERIVATION 9: the staged scanner-execution leg (three-CWD, from the STAGED tree) ---
test -f agents/canary/skills-template/injection_scan.py      # present in the stage worktree at the tag
python3 - <<'PY'
import json
m = json.load(open('<release-dir>/manifest.json'))
hits = [k for k in m.get("agents", {}) if k.endswith("canary/skills-template/injection_scan.py")]
print("scanner in staged payload manifest:", bool(hits), hits[:1])
PY
#   payload containment: task 9 re-hashed the manifest's per-file entries clean, so the shipped
#   copy is byte-identical to the stage-worktree file => the worktree run certifies the payload copy.
SCAN=agents/canary/skills-template/injection_scan.py
for d in . agents /tmp; do                                   # repo root; agents/; a consumer cwd (the staged-gate triple)
  ( cd "$d" && \
    P="$SCAN"; [ -f "$P" ] || P="/home/nea/ensemble-src-wt-stage-<slot>/$SCAN"; \
    python3 "$P" <base-input> | sha256sum )
done
#   the three digests MUST be equal AND equal to the P5 branch-run digests (byte-identity across
#   the branch run, the stage-worktree run, and the payload copy). Executable at stage time: bare
#   python3, no daemon import, no venv, no network, no writes outside stdout -- no
#   deliberately-not-executable escape applies.

# --- boot-smoke rules (if a boot smoke is run during staging) ---
#   ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0 pinned EXPLICITLY (never 'env -u' -- that strips the guard)
#   per-variable env scrub: env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT
#   'env -u POSTGRES_*' is a silent NO-OP -- bash does not glob env names
#   fresh PG + DROP teardown; never resolve live-shaped paths (mktemp FAKE_HOME + escape guard)
```

---

## Coupling

| Direction | Coupling | Notes |
|---|---|---|
| **in** | **P5** (tight) | P6 stages only what P5 verified; if P5's gates are red, P6 does not start. |
| **in** | **P4** | tight — the payload that ships is P4's commit (merged). |
| **in** | **P0** | loose — the base and the merge-overlap premise. |
| **out** | **the user ceremony** | the promote command is recorded with its preconditions; **the lane never runs it**. |
| **↔ memoir lane** | (conditional) | the slot is derived, not reserved; the migration census is reasoned on the **merged** delta; `PACKS.md` conflicts keep-both newest-first. |
| **out** | **P1–P3** | independent — the staging machinery is orthogonal to the lane's content. |

**Handoff artifacts for the user ceremony:** the release directory path, the binary sha, the tag, the
merge and bump commits, the migration census + rollback adjudication, the re-derived agents count,
the prompt-artifact baseline count, the recorded (unexecuted) promote command, and the
disclosures (the 4 base-stale SEALED rows; the roster/version-pin posture; the canary carve-out; the
`auto_load` choice; the slot contention; the conditional memoir overlap).
