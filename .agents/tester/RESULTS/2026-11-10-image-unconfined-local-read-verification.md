# Image tools — unconfined local-path read — TESTER VERIFICATION

**Mission:** `img-unconfined-read` — operator policy change removing local-path **directory confinement**
from `explain_image` + `compare_images`, while KEEPING the URL/SSRF lane, the 10 MB cap (URL **and**
local), magic-byte/MIME validation, and path normalization.

**Verdict: SHIP** (one 🟠 important pre-existing test-debt finding carried forward — see F-1; it is
**not** branch-introduced and **not** a regression).

| | |
|---|---|
| **Branch** | `feature/image-tools-unconfined-read` |
| **Worktree** | `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble-wt-img-unconfined-read` |
| **TIP pin** | `c310fe49231c9f4d2cb55d835a64432cf57dfbb5` (verified == HEAD throughout; **no scope mutation** observed) |
| **BASE leg** | `0efdf106f3f66ccb400405f4137694592b72097d` (branch point; contains the CONFINED tree) |
| **Parent chain** | `c310fe492 ← 32bd02a70 ← 0efdf106f` |
| **Change set** | 11 files, +684 / −575 — 2 product modules, 2 test files, 6 agent prompt artifacts, 1 regenerated baseline |
| **Date** | 2026-11-10 |
| **Gate tier** | **Tier 1** (blast-radius leg) — see Scope Decision |

---

## Scope Decision

The request implied an end-to-end policy verification. The diff touches **no core-seam path**
(`daemon/graph.py`, `daemon/manager.py`, `daemon/api.py`, `daemon/services/instance_messaging.py`,
`daemon/services/task_processor.py`, `daemon/services/job_queue_service.py` → zero hits), so per
`.agents/tester/rules/ensure.md` § Gate Tiers this is **Tier 1**, not Tier 2 whole-tree.

**Ran:** the declared blast radius — `tests/test_image_tools.py` + `tests/test_compare_tools.py`
(199 nodes at TIP / 191 at BASE) on BOTH legs, the 6 edited agent artifacts' adjacent-integrity lanes
(10 census/pin files + prompt-artifact baseline + section-reference integrity + 2 known-red files) on
BOTH legs, plus two purpose-built behavioural A/B packs (local-path + SSRF) that drive the **real public
tool surface** on both legs.

**Skipped:** the whole-tree suite. Not warranted — a 2-module, non-core-seam change. Cost of the full
suite would be ~30 min of 10k+ nodes for no added signal on this change set.

---

## Base-leg provisioning (the load-bearing step)

A shared `.venv` pins the worktree via `_editable_impl_ensemble.pth`, so a naive base run would have
silently executed **TIP code** and produced a false A/B. The base leg was therefore provisioned
separately and gated on a hard import proof.

| Step | Result |
|---|---|
| Base worktree | `git worktree add --detach /tmp/img-ab-base 0efdf106f` → exit 0; HEAD `0efdf106f3f66ccb400405f4137694592b72097d` |
| Per-tree venv | **`uv sync --frozen`** (exit 0) — `--frozen` keeps `uv.lock` byte-identical (`sha256 cfde5fe4…c13392` before AND after); plain `uv sync` could have re-locked and dirtied the tree. No `.venv` copy → no `.pth`/shebang rewrite needed; uv generated a fresh BASE pin (`/private/tmp/img-ab-base`). |
| **Import proof — BASE** | `daemon.__file__ = /private/tmp/img-ab-base/daemon/__init__.py` — INSIDE the tree, from tree cwd **and from neutral cwd `/tmp`** with an absolute venv path (the neutral-cwd run proves the **pin itself** resolves, not `sys.path[0]` shadowing) |
| **Import proof — TIP** | `daemon.__file__ = /Users/…/agents-ensemble-wt-img-unconfined-read/daemon/__init__.py` — INSIDE the tree, both cwds |
| Isolation | `scripts/run_tests_scrubbed.sh` `cd`s to **its own** repo root and `exec`s **its own** `.venv/bin/pytest` → each leg's own script gives per-leg isolation automatically |

macOS `/tmp` → `/private/tmp` is a symlink; every containment comparison used **resolved** paths, never a
literal `/tmp` prefix string.

**House scrubber:** `scripts/run_tests_scrubbed.sh` (present, byte-identical both legs, `sha a58dd7c7…`)
unsets 18 names — `POSTGRES_{HOST,DB,USER,PASSWORD,PORT,URL}`, `DATABASE_URL_POSTGRES`,
`PGHOST PGPORT PGDATABASE PGUSER PGPASSWORD PGPASSFILE PGSSLMODE PGSERVICE PGSERVICEFILE`,
`PLANE_MCP_WORKSPACE_SLUG PLANE_BASE_URL` — then `exec`s pytest, with a `compgen -v` zero-survivor leak
gate (exit 78). The ambient shell carried `POSTGRES_DB=ensemble_prod` / `POSTGRES_USER=ensemble` — the scrub
was **material**, not ceremonial. The `env -u POSTGRES_*` GLOB form is a **silent no-op** in bash and was
never used; per-variable `env -u` only.

---

## AC-1 — Two-directional A/B (core evidence) · ✅ **PASS** (16/16 assertions)

**Method:** one driver, both legs, same order, through the **real public tool surface**
(`create_image_tools(manager, current_instance_id)` → the registered `explain_image`), with only the
outbound delegation stubbed (`daemon.tools.image_tools.invoke_agent_and_wait` patched on the module
object, recording its `images` argument). The manager stub is `types.SimpleNamespace`-based (**not**
MagicMock, which auto-creates attributes and would fabricate a scope root): project `main_directory` =
that leg's tree root, `related_directories = []` — so the ONLY scope root is the tree root.

**Fixtures** (`/tmp/img-ab-fixtures/outside`, outside every project scope — asserted
`fixture_inside_tree: false` on both legs):

```
real.png    73 B  real PNG (magic + IHDR/IDAT/IEND, sha256 97a3a410c9bca540…29)
link.png -> /tmp/img-ab-fixtures/outside/real.png
missing.png        ABSENT (asserted in preflight)
adir/              empty directory
```

| case | BASE `0efdf106f` | TIP `c310fe492` |
|---|---|---|
| outside_readable | **scope refusal**, dispatch **NO** | **ACCEPTED**, dispatch **YES** (`image-reader`), 73 B real PNG `data:` URI |
| symlink → readable outside | **scope refusal**, dispatch **NO** | **ACCEPTED**, dispatch **YES**, same 73 B as its target |
| nonexistent | `Image file does not exist: …` | `Image file does not exist: …` — **no scope vocabulary** |
| directory | scope refusal (see D-1) | `Path is not a regular file: …` |

**BASE scope refusal, verbatim:**
> `Error: Failed to load image '/tmp/img-ab-fixtures/outside/real.png': Path '/tmp/img-ab-fixtures/outside/real.png' is outside the project's allowed directories; it must resolve inside one of [/private/tmp/img-ab-base]. Remedy: copy the image under one of those directories, or add its directory to the project's related directories.`

**Payload authenticity (beyond a prefix check):** the captured TIP data URI was base64-decoded → **73 bytes,
byte-identical** to `real.png` (`sha256 97a3a410c9bca540512251c37ce63982edccbed54c6f2e1d06ec717b9f753e29`,
PNG magic `\x89PNG\r\n\x1a\n` verified); the symlink case decoded to the same bytes as its target.

**Structural corroboration:** BASE still exposes `_is_within_scope` + `_resolve_scope_roots`
(scope computed = `["/private/tmp/img-ab-base"]`); TIP exposes **neither** — only `_get_project_base_dir`.

**`compare_images` (the renamed surface):**

| leg | outcome |
|---|---|
| BASE | `{"error": "Path '…' is outside the project's allowed directories; …", "image_id": "…", "input_slot": "image_a", "kind": "input-not-found", "reason": "workdir_invalid"}` — refused before dispatch, **no dispatch** |
| TIP | `{"verdict": "pass", "per_criterion": [...], "summary": "STUB_COMPARATOR_REPLY", …}` — dispatch **YES** to `image-comparator`, `images_count: 2`, both `data:` URIs |

The rename is **reachable, not cosmetic** — same failure classes, both legs: BASE `workdir_invalid` →
TIP `path_invalid`.

**Negative control (assertion validity):** feeding the assertion layer the legs **swapped** yields
`PACK_ASSERTIONS: FAIL (4/16)` with 12 FAIL lines and exit 1 — the assertions genuinely discriminate the
legs and are not vacuously green.

---

## AC-2 — SSRF floor A/B · ✅ **PASS** (117/117 assertions)

| hostile URL | BASE guard verdict | TIP guard verdict | dispatch either leg? | data URI seen? |
|---|---|---|---|---|
| `http://127.0.0.1/x.png` | `Blocked: hostname 127.0.0.1 resolves to non-public IP 127.0.0.1` | **identical** | NO | NO |
| `http://localhost/x.png` | `Blocked: hostname localhost resolves to non-public IP ::1` | **identical** | NO | NO |
| `http://10.0.0.1/x.png` | `Blocked: … non-public IP 10.0.0.1` | **identical** | NO | NO |
| `http://172.16.0.1/x.png` | `Blocked: … non-public IP 172.16.0.1` | **identical** | NO | NO |
| `http://192.168.1.1/x.png` | `Blocked: … non-public IP 192.168.1.1` | **identical** | NO | NO |
| `http://169.254.169.254/latest/meta-data/` | `Blocked: … non-public IP 169.254.169.254` | **identical** | NO | NO |
| `http://[::1]/x.png` | `Blocked: hostname ::1 resolves to non-public IP ::1` | **identical** | NO | NO |
| `file://<real readable fixture>` | `Image file does not exist: file:///…` | **identical** | NO | NO |
| `ftp://example.com/x.png` | `Image file does not exist: ftp://example.com/x.png` | **identical** | NO | NO |

- All 7 http/https guard verdicts are **exact-string equal** across legs (`guard_ok == false` both legs).
- `_ensure_public_url` is **byte-identical** at both legs.
- Pinned `TestSsrfGuard` class: **same 11 nodes at both legs**, class body byte-identical.
- **Socket instrumentation: `socket_connect_attempts: []` on BOTH legs** — the guard rejects *before* any
  fetch; nothing outbound was attempted.
- **`file://` probe verdict: the TIP leg did NOT read a real out-of-scope file.** The fixture genuinely
  existed and was readable at probe time (`fixture_readable: true`, 67 B, valid PNG magic) — yet no data
  URI and no dispatch. The probe was **non-vacuous** (fixture readability asserted).

**Correction to an earlier prediction (recorded, not smoothed):** the original brief predicted BASE would
answer `file://` with a *scope refusal* and TIP with an honest error — i.e. a leg-to-leg behaviour change.
Empirically **both legs are behaviour-identical** (`Image file does not exist: file:///…`): `pathlib` treats
`"file:///…"` as a **relative** path, so it is joined under the anchor/scope-root where nothing exists, and
BASE's existence check precedes its scope check. The scope-refusal-vs-honest-error delta is real but is
reachable only with bare POSIX paths — exactly what AC-1 measured. The claim under test (no read, no
dispatch, no data URI) holds at both legs.

---

## AC-3 — Cap + MIME floor at TIP · ✅ **PASS** (6/6 assertions)

Fixtures outside every project scope (`/tmp/img-unconfined-fixtures/outside`):
`big.png` **11,534,417 B > 10,485,760 B** cap (a real PNG — chunk stream `IHDR,IDAT,prVt,IEND`, every
chunk CRC32-verified), `notimage.png` 32,000 B of non-image bytes with a `.png` extension, `small.png` 69 B
real PNG (control).

| assert | case | verdict | evidence |
|---|---|---|---|
| **A** | small.png | PASS | dispatch **YES**, payload `data:image/png;base64,iVBORw0KGgo…`, `dispatch_called=True` |
| **B** | big.png | PASS | `cap_mentioned=True`, `scope_vocab_hit=[]`, `dispatch_called=False` |
| **C** | notimage.png | PASS | `magic_mentioned=True`, `scope_vocab_hit=[]`, `dispatch_called=False` |

> **B)** `Error: Failed to load image '/tmp/img-unconfined-fixtures/outside/big.png': Image file exceeds maximum size of 10485760 bytes: /tmp/img-unconfined-fixtures/outside/big.png`
> **C)** `Error: Failed to load image '/tmp/img-unconfined-fixtures/outside/notimage.png': Could not determine image format for '…': magic bytes did not match any supported format.`

Neither rejection string contains scope vocabulary → the rejection reason **is the floor**, not a scope
refusal. Both manager shapes (`scoped` and `noproject`) returned byte-identical strings.

**BASE comparison (the load-bearing half):** BASE refuses the **same** fixtures with scope vocabulary and
**never reaches** the cap/MIME checks (`cap_mentioned=False`, `magic_mentioned=False`,
`scope_vocab_hit=["outside the project's allowed directories", …]`) — or, with no project scope,
`No project workdir or related directories are available — cannot validate local file path for security
(fail-closed).` BASE refusing the identical fixtures as out-of-scope **proves the fixtures are genuinely
outside every scope**, so the TIP floor rejections cannot be a fixture artifact.

---

## AC-4 — TIP suites · ✅ **PASS** (199/199)

```
cd <TIP> && timeout 300 bash /tmp/img-gate-packs/img_tip_suite_test.sh
```
- Slicing proof: `tests/test_image_tools.py` **116** collected · `tests/test_compare_tools.py` **83**
  collected · total **199** — executed **199**, so executed == collected for each file.
- pytest summary (verbatim): **`199 passed in 0.96s`** · pack exit **0** · wall **3 s** · `RESULT: PASS`.
- No failures, no errors, no skips, no xfails.
- Independently re-verified by the tester: log at `/tmp/img-gate-packs/tip_suite.out`, `RESULT: PASS`,
  worktree clean, HEAD unmoved.

---

## AC-5 — Adjacent-integrity lanes (6 edited agent artifacts) · ✅ **PASS** (base-leg proof supplied)

The mission required pre-existing attribution **with base-leg proof** — a diff argument was not accepted.
Every arm was run on BOTH legs with an identical command.

| arm | BASE | TIP | digest (BARE-ID, identical) | verdict |
|---|---|---|---|---|
| **ARM-1** — 10 census/pin files | 271P / 0F / 0E / 24S (295 coll.) | 271P / 0F / 0E / 24S (295 coll.) | empty `e3b0c442…b855` | zero failures both legs |
| **ARM-2** — `.prompt-artifacts.sha256` `--check` | exit 0, **181** entries | exit 0, **181** entries | — | regenerated baseline **correct**; no count drift |
| **ARM-3** — section-reference integrity | 1630P / **5F** (1635 coll.) | 1630P / **5F** (1635 coll.) | `2555d922…263cb` | identical set **and** identical assertion messages |
| **ARM-4** — anomaly-duty prompts | 12P / **7F** (19 coll.) | 12P / **7F** (19 coll.) | `8803bab6…c8b` | identical set + messages |
| **ARM-5** — aegis security family | 555P / **1F** (556 coll.) | 555P / **1F** (556 coll.) | `c9d974d5…be7` | identical set + messages |

`cmp` identical and `comm -23` / `comm -13` **both empty** for every arm → **zero net-new reds across all
five arms.** No file absent at base.

- **ARM-3's 5 reds:** `test_no_bare_md_filename_tokens_in_prompts[workflow.md1]` (`agents/developer/workflow.md`),
  `[workflow.md11]` (`agents/developer[v2]/workflow.md`), `[install-mermaid-cli.md]`
  (`agents/charter/skills-template/`), `test_odd_fence_count_fails[rule.md31]` (`agents/charter/rule.md`),
  `[workflow.md31]` (`agents/charter/workflow.md`). **None names any of the 6 edited artifacts**, and the
  mechanism is byte-identical too (same fence-line lists, e.g. `agents/charter/workflow.md has 41 ``` fences
  (odd) … L40, L50, L103, … L664`).
- **ARM-2:** `PROMPT-BASELINE: OK — 181 versioned artifact(s) match .prompt-artifacts.sha256` — every one of
  the 181 pinned sha256 rows matches the real bytes on disk, including the 6 regenerated rows.
- **Positive control:** `test_aegis_security_family.py::TestWorkerUntouched::test_worker_untouched`
  **PASSES with this mission's `agents/worker/tools_note.md` edit** — it pins the exact changed-file set
  under `agents/worker/`, so the worker prompt edit is directly pinned green, not merely untested.

---

## AC-6 — Pre-existing reds · ✅ **attributed pre-existing WITH base-leg proof**

| file | expected | observed TIP | observed BASE | verdict |
|---|---|---|---|---|
| `tests/unit/test_anomaly_report_duty_prompts.py` | 7F | **7F** / 12P | **7F** / 12P | identical set + identical messages → pre-existing |
| `tests/unit/agents/test_aegis_security_family.py` | 1F | **1F** / 555P | **1F** / 555P | identical set + identical message → pre-existing |

No deviation from the expected counts. Neither file is touched by this diff. **Root cause of the 7 ARM-4
reds:** `agents/canary/rule.md` never received the canonical nest-anomaly-report duty block (it landed after
pin commit `400e6482b`), making it the 46th dir (45→46) and the 44th walk member (43→44) and the file behind
4 of the 7. The 177→181 baseline delta is **exactly canary's four versioned artifacts** (177 + 4 = 181), and
trunk is also 181 — so regenerating the baseline does not and did not fix that red: **the pack's 177-entry
expectation is the stale side.** See F-2 / F-3.

---

## Tier-1 dual-side FAILED-digest leg · ✅ **zero net-new reds**

Identical command string on both legs, only cwd differs:
```
scripts/run_tests_scrubbed.sh tests/test_image_tools.py tests/test_compare_tools.py -q --tb=short -p no:cacheprovider
```
No `-m` (would replace the addopts lane), no `-x`, BASE first then TIP, strictly sequential.

| leg | summary (verbatim) | exit | collected | passed | failed | errors | skipped |
|---|---|---|---|---|---|---|---|
| BASE `0efdf106f` | `191 passed in 0.82s` | 0 | 191 | 191 | 0 | 0 | 0 |
| TIP `c310fe492` | `199 passed in 1.05s` | 0 | 199 | 199 | 0 | 0 | 0 |

Progress output confirms real execution (BASE 72+72+47 = 191 dots; TIP 72+72+55 = 199 dots; zero `F`/`E`/`s`/`x`).

Digests — **(FORM, sha256) pairs, never a bare sha**; `e3b0c442…b855` is the empty-string digest:

| FORM | file | sha256 | bytes |
|---|---|---|---|
| BARE-ID | base.nodeids.txt | `e3b0c442…b855` | 0 |
| GREP-FULL | base.grepfull.txt | `e3b0c442…b855` | 0 |
| BARE-ID | tip.nodeids.txt | `e3b0c442…b855` | 0 |
| GREP-FULL | tip.grepfull.txt | `e3b0c442…b855` | 0 |
| BARE-ID / INTER-168 | base.intersection.nodeids.txt | `e3b0c442…b855` | 0 |
| BARE-ID / INTER-168 | tip.intersection.nodeids.txt | `e3b0c442…b855` | 0 |

`cmp` IDENTICAL (raw and intersection-restricted); `comm -23` (net-new) **∅**; `comm -13` (fixed) **∅**.
Both prefixes captured (GAP-2 satisfied — the `^FAILED `-only blind spot does not apply).

**D1 / D2 two-class declaration:**
- **D2 (dual-side comparable) = 168 node ids** — carries the FAILED-digest diff. Net-new ∅.
- **D1 (lane-owned, TIP-only) = 31 node ids** — `TestExplainImageUnconfinedPath` ×10,
  `TestLocalPathNoConfinement` ×8, `TestPathInputUnconfined` ×5, `TestInputResolutionPathReasons` ×3,
  `TestExplainImageDocsDropConfinement` ×3, `TestCompareImagesDocsDropConfinement` ×2. All 31 executed and
  were green at TIP (199 passed / 0 skipped / 0 deselected → not skipped into a false green). A BASE-side
  red is impossible by construction → recorded as **expected-by-construction**, neither a pass nor a failure.
- **BASE-only = 23 node ids**, the confinement-era suite the branch replaced — `TestExplainImagePathScope`
  ×12, `TestPathTraversalGuard` ×6, `TestInputResolutionWorkdirReasons` ×3,
  `TestWorkdirScopeIncludesRelatedDirectories` ×2 — **all green at BASE** as well.

**QUARANTINE.md:** 180 lines; `grep -niE 'image_tools|compare_tools|test_image|test_compare|explain_image|compare_images'`
→ **NO MATCH**. Neither test file is quarantined; no quarantine interaction.

---

## Findings

| id | severity | finding | status |
|---|---|---|---|
| **F-1** | 🟠 important | **The URL-lane 10 MB cap is pinned by NO test.** `grep -rn '_load_image_from_url' tests/` → **0 hits at BASE *and* TIP**. `tests/test_image_tools.py::TestMemoryCap`'s docstring overclaims *"Tests for the 10MB size cap (URL and local path branches)"* while both of its tests are local-only (and the oversize test **mocks `Path.stat`** rather than writing a real file). **Attribution: PRE-EXISTING, not branch-introduced** — AST-extracted source segments are **byte-identical at both legs**: `_ensure_public_url` (`cab86e9fe72d9e32`), `_load_image_from_url` (`53f009a283efac4a`), `_MAX_IMAGE_BYTES` (`5dfe80db74d6474c`), `_detect_format_from_url` (`4f62528c7811`). The URL-branch dispatch line is unchanged; only the local-path call gained `base_dir`. **Not a regression, but a real test-debt hole on the lane the mission claims to have kept.** The *local* cap is now proven by a real 11,534,417-byte file — strictly stronger than the mocked-`stat` variant. |
| **F-2** | 🟠 important | **`agents/canary/rule.md` never received the canonical nest-anomaly-report duty block.** Live trunk obligation (not diff-induced) behind 4 of the 7 ARM-4 reds. The pack's tripwire did its job; the obligation is still unpaid. **Filed, not closed.** |
| **F-3** | 🟡 minor | **The 177-entry baseline expectation in the anomaly-duty pack is the stale side.** Trunk is 181; the regenerated 181-entry file matches disk bytes on all 181 rows (ARM-2 OK at both legs). The pin, not the artifact, needs re-baselining. |
| **F-4** | 🟡 minor | 5 pre-existing prompt-section-reference reds (`agents/charter/{rule,workflow}.md` odd fence counts; `agents/developer{,[v2]}/workflow.md` + `agents/charter/skills-template/install-mermaid-cli.md` bare-md tokens) — byte-identical at both legs. |
| **F-5** | 🟡 minor | **PACKS.md registration gap.** `tests/test_image_tools.py` and `tests/test_compare_tools.py` are in **no registered pack** — the F-2 "blind-spot class" (a surface no gate can see). Both were run here as ad-hoc scratch packs under `/tmp/img-gate-packs/` with the full dual-layer contract, **not** committed (the mission requires a path-limited RESULTS-only commit that must ride the merge). Registering a pack needs a decision on where it lands. |
| **F-6** | 🟡 minor | **POK store is at its 64/64 active-entry cap** — `pok_upsert` refuses all writes (`pok_active_cap`); only a leader/PM can archive. A contradicting pair was identified: `install-dir-01-relative-path-allowed` ("ALLOWS a relative INSTALL_DIR … UNMERGED") vs leader-authored `install-dir-02-relative-path-refused` ("arm 3b ≡ F6 refuses relative INSTALL_DIR, exit 78, SHIPPED in v0.29.10"). Archive is forward-only, so the first is the candidate to supersede. |
| **F-7** | 🟡 minor | **`wt.claim.img-unconfined-read` row is stale** — `wt_branch_head` records `0efdf106f` (the branch point) while the real tip is `c310fe492`; `heartbeat_at` is ~2.7 h stale. |
| **F-8** | ⚪ cosmetic | Foreign stale `.pyc` residue in the TIP tree's `tests/__pycache__/` from a prior worker (`_baseleg_test_image_tools`, `_baseleg_test_compare_tools`, …) — their `.py` sources are already deleted. Gitignored (worktree stays clean); harmless for collection (AC-4 collected exactly 116) but it pollutes any `grep -rl` over `tests/` and is evidence that a **prior attempt ran BASE legs inside the TIP tree** — the exact wrong-tree trap this gate was provisioned to avoid. Left in place (not purged). |
| **F-9** | ⚪ cosmetic | ARM-1 summary line differed by a **warnings block only**: BASE `271 passed, 24 skipped, 1 warning in 11.65s` vs TIP `271 passed, 24 skipped in 11.12s`. Cause: a cold-compile `SyntaxWarning: invalid escape sequence '\`'` from `tests/unit/tools/test_privileged_category_system_log.py:38` (BASE had no `.pyc`; TIP had a warm one). **Tallies identical (271/0/24) — no behavioural delta.** |

---

## Deviations from the briefs (recorded, not smoothed)

- **D-1 — BASE's directory case returns a *scope refusal*, not "not a regular file".** BASE orders the
  scope-containment check **before** `stat.S_ISREG`, so an out-of-scope directory is refused as out-of-scope.
  The security property holds (refused, zero dispatch, no data URI); the brief's literal expected BASE
  string was simply wrong for an out-of-scope directory. Exercising BASE's `S_ISREG` branch would require a
  directory **inside** the tree root, which the fixtures deliberately are not. TIP has no scope check and
  reaches `S_ISREG` → `Path is not a regular file: …`.
- **D-2 — the `file://` leg-to-leg difference predicted in the brief does not exist** (both legs are
  behaviour-identical — see AC-2). The corrected mechanism is recorded above.
- **D-3 — a worker pack leaked an orphan `sleep 270`** (its own watchdog's child survived `trap … EXIT`).
  Self-inflicted, `/tmp`-only, fixed by killing the watchdog's children; the pack was re-run in full and
  the PASS above is the post-fix run; `ps` showed 0 orphans. No tree file touched.
- **D-4 — the Tier-1 leg ran serially, not `-n auto` within a leg** (house canon §(d) prefers xdist): the
  identical-command-string invariant is what carries the proof, and `pytest-randomly` is **not installed**
  in either leg's venv, so there is no ordering confound.
- **D-5 — the BASE adjacent-lanes run set `PYTHONDONTWRITEBYTECODE=1`** (tree-hygiene) while the TIP leg did
  not. Verified no semantic impact: 0 `__pycache__` dirs written in the pack window, `git status` empty.

---

## Environment, safety and fences

- **No deploy, no daemon boot, no daemon restart, no signal to any daemon.** `~/agents-ensemble` (live
  install) never entered. No `make install/uninstall/deploy*`. No process killed except a worker's own
  orphan `sleep` child. Port 8088 untouched; no listener on 8079/8094/8088.
- **MAIN checkout never modified.** Its ` M .agents/tidier/notes.md` is **pre-existing** dirt (mtime ~1 h
  before this session began) — the known tidier EOF-append trap; left untouched.
- Both repo trees clean at close: TIP `git status --porcelain` empty, HEAD `c310fe492`; BASE empty, HEAD
  `0efdf106f`. **Nothing committed anywhere except the path-limited RESULTS commit described below.**
- No network fetch of remote images; no DB write; fixtures confined to `/tmp`.
- Every pack ran under a dual-layer timeout (command-level ≤ 240 s, script-internal 270 s); no pack exceeded
  the 5-minute cap; the longest observed wall was 54 s.
- No product code was modified and no fix was applied — findings were returned to the mission owner.

---

## Reproduction

| artefact | path |
|---|---|
| TIP pack (AC-4) | `/tmp/img-gate-packs/img_tip_suite_test.sh` · log `tip_suite.out` |
| A/B behavioural pack (AC-1) | `/tmp/img-gate-packs/img_ab_behavior_test.sh` · driver `ab_probe.py` · `ab_base.jsonl` / `ab_tip.jsonl` |
| SSRF pack (AC-2) | `/tmp/img-gate-packs/img_ab_ssrf_test.sh` · driver `ssrf_probe.py` · `ssrf_base.jsonl` / `ssrf_tip.jsonl` |
| Cap/MIME pack (AC-3) | `/tmp/img-gate-packs/img_capmime_tip_test.sh` · driver `capmime_probe.py` · `capmime.out` / `capmime.base.out` |
| Adjacent-lanes pack (AC-5/6, TIP) | `/tmp/img-gate-packs/img_adjacent_lanes_test.sh` · `adjacent.out` |
| Adjacent-lanes pack (AC-5/6, BASE) | `/tmp/img-gate-packs/img_adjacent_lanes_base_test.sh` · `adjacent_base.out` |
| Tier-1 digest pack | `/tmp/img-gate-packs/img_tier1_digest_test.sh` · `digest_base.out` / `digest_tip.out` |
| Collection census | `/tmp/img-ab-base-collect.txt` (191) · `/tmp/img-ab-tip-collect.txt` (199) · `/tmp/img-ab-both-collect.txt` (168) · `/tmp/img-ab-base-only.txt` (23) · `/tmp/img-ab-tip-only.txt` (31) |
| Base leg tree | `/tmp/img-ab-base` @ `0efdf106f3f66ccb400405f4137694592b72097d` (import-proven, left intact) |
| Fixtures | `/tmp/img-ab-fixtures/outside/` · `/tmp/img-unconfined-fixtures/outside/` · `/tmp/img-ab-ssrf-fixtures/outside/` |

**Base-leg teardown:** the base worktree was deliberately **left intact** (not removed) — it is the
re-runnable base leg for this mission and removing it would destroy the reproduction path. Its metadata
lives in the repo's `.git/worktrees/`; `git worktree remove /tmp/img-ab-base` + `git worktree prune` clears
it when the mission closes.

---

## Verdict

**SHIP.**

- **AC-1** two-directional A/B — ✅ 16/16, real payload byte-verified, negative control confirms the
  assertions discriminate the legs.
- **AC-2** SSRF floor unchanged — ✅ 117/117, byte-identical guard verdicts, zero outbound socket attempts.
- **AC-3** cap + MIME still bind on unconfined reads — ✅ 6/6, with the BASE leg refusing the same fixtures
  as out-of-scope to prove the fixtures are genuinely outside scope.
- **AC-4** TIP suites — ✅ 199/199 (116 + 83), zero deviation.
- **AC-5** adjacent-integrity lanes — ✅ clean ARM-1/ARM-2; the ARM-3 reds are pre-existing **with
  base-leg proof**.
- **AC-6** pre-existing reds — ✅ 7F / 1F at BASE **and** TIP, identical sets and messages.
- **Tier-1 dual-side digest** — ✅ zero net-new reds (D2 net-new ∅, D1 31/31 green).
- **AC-7** — this artifact, committed path-limited on the branch.

**Carried forward for the mission owner (not blockers):** F-1 (URL-lane cap unpinned — pre-existing test
debt), F-2 (`agents/canary/rule.md` duty block — live trunk obligation), F-3 (stale 177 pin), F-5 (pack
registration gap), F-6 (POK cap — needs leader/PM archive), F-7 (stale claim row).
