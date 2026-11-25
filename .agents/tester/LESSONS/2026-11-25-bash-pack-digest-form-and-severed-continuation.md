# LESSONS — 2026-11-25 — promote hand-back landing gate (M = 8d306f2e)

Two findings from the FULL HOUSE gate at the promote-handback false-halt landing merge.
Both are **method** findings: they change how gates are run, not just this lane's result.

---

## L1 — The canon digest forms are VACUOUS for bash packs (silent net-new-red blindness)

**The trap.** The house digest recipe (`.agents/tester/skills/failed-digest-equality-gate/`,
and the blast-radius-leg recipe that inherits it) mandates two digest forms over
`grep -E '^(FAILED|ERROR)'`:

- **BARE-ID** — sorted node ids, prefix stripped
- **GREP-FULL** — the raw grep output

Both forms assume **pytest** output, where a failed node prints a `FAILED <file>::<test>`
line. A **bash pack** prints its own summary block instead:

```
FAILED TESTS:
  ✗ S5 start hang: subprocess_wait_timeout event with site=handback_start
  ✗ S5b the timeout row stands as the honest record
```

The literal `^(FAILED|ERROR)` grep matches **only the header line** `FAILED TESTS:` —
one line on every leg, whatever the failure set. So the digest is byte-identical on two
legs with **completely different failure sets**, and the gate reports "no delta" while
two net-new reds walk through it.

**Measured instance.** `tests/test_bounded_subprocess_waits.sh` at M = `8d306f2e` vs base
P1 = `35ec50e3c`:

| form | base (74P/1F) | M (72P/3F) | verdict it produced |
|---|---|---|---|
| BARE-ID over `^(FAILED\|ERROR)` | `81126c5c…ae8a` | `81126c5c…ae8a` | **identical — WRONG** |
| GREP-FULL over `^(FAILED\|ERROR)` | `05349318…c1a5` | `05349318…c1a5` | **identical — WRONG** |
| **PACK-NATIVE** (`✗ <name>` ids, sorted) | `10aff3e6…6348` | `badb3538…8b78` | **differs — CORRECT** |

`comm` on the pack-native form: **2 head-only (net-new)**, 0 base-only. The two net-new
reds were the real C1 defect (see L2) — invisible to the canon forms.

**Rule (from now on).** For a bash pack, the digest form MUST be the **pack-native
failure-id form** (whatever the pack prints per failing case — `✗ <name>`, `FAIL: <name>`,
`not ok <name>`), and the report MUST state the form it used and why. Never report a
bash-pack leg as "digest-identical" on the strength of a `^(FAILED|ERROR)` grep alone.
If the pack prints no per-case id, the leg has no usable digest — say so instead of
falling back to the header line.

**Where the canon is silent.** The recipe's GAP-2 fold (digest BOTH `FAILED` and `ERROR`
prefixes) fixes a *pytest* blind spot; it does not anticipate the *bash* blind spot.
A follow-up lane should extend the recipe with a "digest form is pack-family-dependent"
clause and a bash-pack example.

---

## L2 — A bash line continuation severed by an intervening comment block is SYNTACTICALLY VALID

**The trap.** In bash, `\`+newline is a line continuation and `#` starts a comment to
end-of-line. Put a comment block between a continued command and its argument string and
the construct still *parses*:

```bash
journal_history_append subprocess_wait_timeout \
    # RESOLVED H3 (branch dynamic reason × L 0f133d2b9/865ec4ad3
    # … five more comment lines …
    "site=handback_start kind=budget reason=child: $(…) unit=$unit rc=$sbrc — …" \
    || _warn "subprocess_wait_timeout journal event append FAILED (best-effort)"
```

Bash removes the `\`+newline, the rest of that logical line becomes a comment, and so:

1. `journal_history_append` is invoked with **argc=1** — the detail payload is LOST;
2. the orphaned payload string **executes as a shell command** → `command not found` (rc 127);
3. the `|| _warn` fallback fires a misleading *"journal event append FAILED (best-effort)"*;
4. the journal row that lands is **content-less**: `{"event":"subprocess_wait_timeout","detail":""}`
   — no `site=`, no `kind=`, no `reason=`, no `rc=`.

**Why every static gate missed it.**

| gate | result | why |
|---|---|---|
| `bash -n scripts/upgrade/lib.sh` | **exit 0** | the construct is syntactically valid |
| evil-merge line-set comparison | **0 evil outside the ledger** | the comment lines are *inside* the H3 conflict region — they are mapped, legitimate-looking resolution content |
| conflict-ledger textual review | "KEPT / byte-equivalent" | a *textual* reading cannot see that the argument is now a comment |
| the landed reader `tests/test_bounded_subprocess_waits.sh:450/:496` | **RED** | this one DID catch it — it greps the journal row for `site=handback_start` |

Only a **runtime drive** of the resolved call site catches it.

**The rule (merge reviews).** A merge resolution that inserts content *between* a
backslash-continued command and its arguments must be proven at runtime, not by
`bash -n` and not by a line-set diff. Minimum proof: extract the resolved construct and
run it with a stub, asserting the received **argc** and the **payload**, plus a
whole-file scan for the pattern `\`-newline followed by a `#` line.

**Attribution shape.** The defect was merge-introduced (absent at both parents, present
in the H3 resolution region), it survived `bash -n`, it survived the evil-merge audit
(because the content *was* mapped to a ledger row), and it was caught only by
(a) a runtime harness drive and (b) the pre-existing landed reader. Both catchers must
exist for a conflict resolution to be trustworthy.

---

## L3 — A gate leg can be "green" and still be blind (scope honesty)

`tests/test_launcher.sh` passed 519/0/1 on both legs — and the pack **cannot see the cure
at all**: `launcher.sh` sources nothing from `scripts/upgrade/lib.sh`, and the pack
sources only `launcher.sh` + `tests/upgrade_test_lib.sh` (byte-identical subjects at base
and head). A green 8w proves **no regression**; it never proves the cure works.

Record the subject-identity proof in the gate report (sha256 of the pack's inputs on both
legs) so a green leg is never mistaken for cure-verification. Cure-verification lives in
the packs that drive the changed surface: the lane-gate legs pack, the new
`promote_handback_false_halt_repro_test.sh` harness, and the bounded-wait reader.
