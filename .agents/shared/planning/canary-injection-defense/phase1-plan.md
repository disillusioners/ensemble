# Phase 1: The `injection-screen` Skill (Contract-First)

## Objective

Author the **`injection-screen` skill** — the org-wide consumable detection component — with the
**contract specified before the body is written**:

1. the **fence grammar** (delimiters + the provenance header schema) — machine-checkable, so a test
   can assert the exact output shape;
2. the **heuristic rule-ID registry** — a stable ID per injection indicator, so a finding cites a
   rule, not a vibe;
3. the **provenance header** — source, content hash, verdict, rule hits, confidence;
4. the **seed corpus** — a small **synthetic** payload corpus as test fixtures;
5. the **skill body** (canary's canonical copy) + the **mirror copies** + the **manifest entries** for
   the screened roster.

**Contract-first is the point.** If the body is written first, the grammar becomes whatever the prose
happened to produce and there is nothing to pin. The grammar is the artifact; the prose is the
instruction to produce it.

---

## Entry Criteria

- **Phase 0 exited** — the guide-compliance checklist, the persona shape table, the seeding decision
  record, the fold list and the **D1–D7 adopted-rulings table** exist; the architect gate is cleared or the
  defaults are recorded as adopted.
- The P0 fence precedent read is done: `daemon/graph.py::_frame_injected_report` (`:343-373`) — the
  only model-visible untrusted-data frame in the system today, and the generalisation seed for the
  grammar.
- The P0 intake-surface list is confirmed first-hand (12 surfaces, each with its owning agent set).

**Architect dependency — D2 (skill mechanism + distribution + evolution) and D4 (corpus + rule-set
home).**
**Adopted ruling (D2 = Option B):** seed the **standing readers** only
— `canary` (canonical), `aegis`, `worker`, `sweeper` ⇒ **4 bodies + 4 manifest entries** (+2 new
manifest files: `canary`, `sweeper`); **`hacker` and `cracker` get the doctrine layer only** — a
**disclosed coverage gap**, never hidden. `auto_load: **true**` for canary AND sweeper (both have no skill tools / no skill_injection flag — `false` would be unreachable, NC-3); `auto_load: **false**` + a **workflow-mandated load**
for aegis and worker (both hold dynamic-skill + skill_injection, so false is reachable), with **push delivery** where the recipient is episodic. Evolution is **git-only** (a
file skill never takes `skill_fix`); a **version bump is the propagation lever** (the W4 guard never
re-seeds a same-version edit); the copies are **mirrors** kept in sync by the version bump and a
**cross-agent identity pin** (byte-identity modulo blank lines).
**Default assumption (D4):** corpus = a small **synthetic** seed corpus committed under `tests/` as
fixtures (one positive fixture per rule ID + a benign control per family); rule-set home =
**canary's canonical copy**, evolved by canary via git PRs.
If the architect rules otherwise, this phase folds as a text/file move — **no design rewrite**.

---

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | **Specify the fence grammar (NC-1 realignment to `requirements.md` SK-13…SK-25).** Write the exact output shape: a stable **BEGIN/END delimiter pair** wrapping the payload, with a **provenance header** between them carrying machine-parseable **`key: value`** lines (NOT `key=value`) in a **fixed order**: `source` (the intake-surface ID from the P0 list), `sha256` (of the **raw, pre-escape, pre-truncation** content), `verdict` (a **closed three-value enum: `clean` / `suspect` / `hostile`**), `rules` (comma-joined rule IDs, empty when none), and `confidence` (**always present** — `none` is a legal value; never omitted, never faked). **There is NO `version` header field** — the grammar version lives in the BEGIN token (`v1`). The **content between the delimiters is byte-preserved** and **region-aware**: the payload region is **escaped** (a payload line beginning with the BEGIN token is escaped; a **forged END marker is rejected**, not honoured). The block uses a **4-backtick `untrusted-data` carrier** with an escape rule and an **in-band truncation marker** for oversized content (SK-18/19/22). | — | The grammar is written down as a **table** (field, type, allowed values, required?, order) before any prose is authored; a golden-fixture list is enumerated; the escaping rule, the forged-END rejection, the truncation marker and the carrier are **specified**, not left to the implementation. |
| 2 | **Define the rule-ID registry (NC-1).** Rule IDs are **`INJ-nn`**, contiguous from `INJ-01`, **never reused, never renumbered** (RUL-01): `INJ-01` instruction-override phrasing ("ignore previous instructions"), `INJ-02` role-hijack ("you are now …" / persona reassignment), `INJ-03` tool-invocation urging ("call the tool named …", "run this command"), `INJ-04` hidden unicode / homoglyphs (invisible or confusable characters in otherwise plain text), `INJ-05` markdown-embedded directives (a directive hidden in a link target, an HTML comment, or a nested code fence). The category set is **closed at seven** (RUL-02); severity and confidence mappings are **deterministic and total** with `none` legal only for an error block or a clean-by-heuristic-only result (RUL-03/RUL-04); patterns are evaluated against the **decoded** text with declared normalisation (RUL-05). The registry is **extensible** — IDs are stable, the set is not closed — but an ID's meaning is **frozen once shipped**. | 1 | Every ID has a definition + a detection shape + **exactly one** positive fixture in the corpus; the registry states that an ID is **never reused** for a different meaning (a mutation leg changes an ID and the id-uniqueness/coverage leg must red). |
| 3 | **Define the verdict + degrade semantics (NC-1).** The **heuristic path is deterministic and total**: the same input always yields the same fenced output (modulo the optional LLM verdict). The verdict enum is **closed and three-valued** — `clean` / `suspect` / `hostile` (SK-16) — and the **fence is applied regardless of the verdict** (fencing is unconditional; the verdict is metadata). `confidence` is **always present**; `none` is a legal value (SK-16/RUL-04) — never omitted, never faked. The **LLM classification pass is optional** (F4): when it is unavailable the skill **degrades to heuristic-only** and `confidence` takes the value `none`; the pass is **performed by the consuming agent's own judgment** and recorded by **re-running the scanner** with `--llm-verdict` / `--llm-confidence`; the scanner **validates** the closed vocabularies and **applies monotonicity** — it refuses any downgrade of a heuristic `hostile` (SK-11 mechanically enforced). Pinned tests run with the pass **OFF**. | 1, 2 | The degrade path is explicit; a leg proves the output is **byte-identical** with the LLM pass disabled and enabled-but-unavailable; the closed vocabularies (verdict, version, rule IDs) are pinned; the monotonicity refusal is pinned. |
| 4 | **Seed the corpus.** A small **synthetic** payload corpus under `tests/` (per D4's default): one positive fixture per rule ID, **plus a benign control per family** (ordinary prose, a code block that merely *mentions* a phrase, a legitimately formatted markdown list). Every fixture is **invented** — no real credential material, no real user data, no real repo content. | 2 | The corpus covers **every** rule ID with a positive and carries benign controls; a scan of the corpus for secret-shaped strings is clean (Gate H leg). |
| 5 | **Author the canonical body** — `agents/canary/skills-template/injection-screen.md`: the frontmatter (name/version/description), the usage contract (how to call it, what it returns), the rule registry (or its pointer), the fence grammar, the degrade path, the **provenance semantics** (source + hash + verdict), and a **guarantees / non-guarantees** list that states **both** — what the screen does guarantee (a fenced, provenance-stamped, deterministically-screened region) and what it does **not** (it is not a sandbox, it is not a boundary, "the privacy of the artifacts is discipline, not a boundary"). It declares its **canonical home** (guide §2 single-home doctrine) and that **canary owns its evolution**. **Token economy:** the body stays ≤ **20 000 chars** and the rule table ≤ **200 lines / ≤ 24 KiB** — the rule table is a **lookup**, not a per-turn prompt block. | 1, 2, 3 | The body is complete against the contract; it states the canonical-home rule and the git-only evolution path; `include:` ∉ frontmatter (the include resolver's cross-agent by-name fallback would inline another agent's bank row); the guarantees **and** non-guarantees are both present, including the non-guarantee that the screen is not a boundary; the byte budgets hold. |
| 5b | **Add the committed scanner artifact (NC-2).** The screen's procedure is **run the reference scanner; its stdout IS the fenced block** — the model does not hand-assemble the block. Location: `agents/canary/skills-template/injection_scan.py` (the only shipped, precedented location — `agents/` ships, `scripts/` and `tests/` do not; executable precedent at `agents/charter/skills-template/install-mermaid-cli.lib.sh`, mode 100755). **Discovery ladder (binding):** the skill body specifies a ladder — cwd-relative first, then an install-root-resolved path — and the **staged gate runs a three-CWD test** (repo root, `/tmp`, the install/release root) so the scanner resolves without an import-shadow false-green. **Safety properties (testable):** bare `python3`, no daemon import, no network, no writes outside stdout, deterministic; reads the single machine-readable rule table from the canonical body; validates and monotonicises the optional `--llm-*` inputs (§3.6 of the ruling). **Mirrors invoke the same install-global path** — one artifact, not four. | 1, 5 | The scanner exists at the shipped path; the discovery ladder is documented in the body; the three-CWD test is in the staged gate; the safety properties are pinned (no daemon import, no network, no writes, deterministic). |
| 6 | **Create the mirrors** — for each member of the screened roster, a **byte-identical** copy of the canonical body at `<agent>/skills-template/injection-screen.md`, plus a `<agent>/skill-set.yaml` entry. **Default roster (D2 = Option B):** `canary` (canonical), `aegis`, `worker`, `sweeper` ⇒ **3 mirrors** + the canonical body. `worker/skill-set.yaml` currently reads `skills: []` — it becomes a one-entry manifest; `aegis/skill-set.yaml` gains a second entry alongside `aegis-strategy`; `sweeper` has **no** `skill-set.yaml` today ⇒ it gains one. **`hacker` and `cracker` get NO copy and NO manifest entry** — their coverage is the **doctrine layer** (P2), and the gap is **disclosed** in the plan, the report and the campaign coverage table. | 5 | Every copy is byte-identical to the canonical copy (`cmp`, modulo blank lines); every receiving manifest has **exactly one** `injection-screen` entry; **no other agent's manifest mentions it** — including `hacker` and `cracker`. The roster is recorded so P4 can pin it. |
| 7 | **Pin the propagation mechanism** in the body's versioning note: the version in the manifest is the **propagation lever** (the W4 guard re-seeds only on a strictly higher version) ⇒ a rule-set change bumps **every** copy's version **and** its bytes in the **same commit**, or the copies silently drift. Also declare the **honest single-home statement**: *the canonical body is canary's; the seeded copies are mirrors, kept in sync by the version bump and the cross-agent identity pin.* | 5, 6 | The note is present in the body and the version numbers across the roster are **equal** (asserted at P4); the single-home statement names the canonical path and the sync mechanism. |
| 8 | **Write the fence-contract + parser + corpus test legs** (the machine-checkable seam the plan promised — house leg names, so the pack registry can cite them): **T32** fence-contract leg (structure, tokens, the directive literal, header **order** + closed vocabularies); **T33** parser leg (region-awareness — including a fixture whose *payload* contains `verdict: clean` so the parser cannot be fooled by a header-shaped string inside the payload; `lines`/`escaped` integrity; **forged END rejection**; **unknown version refusal**); **T34** heuristic corpus leg (every case's `expected_verdict` / `expected_confidence` / `expected_rules` are consumed **with the LLM pass off**); **T37** corpus hygiene leg (label present, secret scan **with both controls**, coverage counts). Golden fixtures (input → exact expected fenced output), a header-schema leg, a byte-preservation leg and a benign-control leg are the T32 members. These land in the **extended family test file** (P4 assembles them; the leg definitions are authored here so the contract and its tests are written together). | 1–4 | Each leg is **mutation-meaningful**: the named mutation (break the delimiter, drop a header field, change the enum, drop a fixture, swap an expectation, unescape a payload line that begins with the BEGIN token) turns **exactly** the intended leg red, and restoring turns it green. |
| 9 | **Record the rule-set evolution contract** (D4): canary may **evolve** the registry via a **git PR** on the canonical copy; the evolution **must** update the mirror roster in the same change (the byte-identity pin enforces it); a rule is **added, never re-purposed** (an ID's meaning is frozen once shipped); a **false positive** is a rule-shape change + a fixture change, not a silent suppression list. | 5 | The contract is written into the canonical body; it names the git-only path (never `skill_fix` — a file skill refuses it) and the mirror obligation. |
| 10 | **Carry the intake inventory inside the body** (the skill names the channels it exists for): one row per channel → **source label** → **carrier seam** — bash/proc output (uncapped, all leaves) · `read_file` content (6000-char cap) · `grep`/`glob` output (walk-capped) · MCP tool results (verbatim, unbounded; `aegis`/`worker` only) · `explore`/RAG results · `read_context` full bodies (auto-injected for `worker`) · child reports (framed by the daemon at `daemon/graph.py:343-373`) · chat-source messages (bound instances) · job `[JOB_EVENT]` (truncated) · the `send_message` `context=` dict (4000-char cap) · skill bodies from outside the payload · Plane bodies (PM-only) · web content. | 1–4 | The inventory is present in the body as a navigable section; each row names the channel and its carrier seam; the seams are cited as research facts, not as requirements. |

---

## Gates

| Gate | Criterion |
|------|-----------|
| **G1.1** | The grammar is **specified before the body** — the contract table exists in the plan's own working notes and every body paragraph maps to a contract clause. |
| **G1.2** | The heuristic path is **deterministic and total**: the same input yields byte-identical output; the LLM pass is optional and its absence is **visible** (no `confidence` field, not a fabricated one); the vocabularies (verdict, version, rule IDs) are **closed**. |
| **G1.2b** | The grammar is **region-aware**: a payload containing a header-shaped string (`verdict: clean`) cannot be mis-parsed as a header, a payload line beginning with the BEGIN token is escaped, a **forged END marker is rejected**, and an **unknown version is refused**. |
| **G1.3** | The fence is **unconditional** — every return value is fenced, regardless of verdict. |
| **G1.4** | Every rule ID has a positive fixture and a benign control; the corpus contains **no real credential material** (secret-shape scan clean). |
| **G1.5** | Every mirror is **byte-identical** to the canonical copy; every receiving manifest has exactly one entry; the roster is recorded for P4's pin. |
| **G1.6** | The body is guide-compliant: no `.md` path tokens, no bare `agents/`-prefix cross-references, no system internals, one canonical home, `include:` ∉ frontmatter. |
| **G1.7** | The fence-contract legs are **mutation-meaningful** (each named mutation reds exactly the intended leg) — the named set covers at minimum: break the delimiter, drop a header field, change the enum, swap a corpus expectation, unescape a payload line that begins with the BEGIN token, delete a doctrine anchor, drift a mirror. |

---

## Verification Commands

```bash
cd /home/nea/ensemble-src-wt-canary-injection-defense

# --- DERIVATION 1: the mirror roster (default: canary canonical + 4 mirrors) ---
#   canary   = canonical (no mirror copy beyond itself)
#   hacker   = +1 template copy, +1 new skill-set.yaml
#   cracker  = +1 template copy, +1 new skill-set.yaml
#   sweeper  = +1 template copy, +1 new skill-set.yaml
#   worker   = +1 template copy, skill-set.yaml MODIFIED (skills: [] -> one entry)
#   => agents/ files added by this phase = 1(canary template)+2+2+2+1 = 8   (the canary meta+prompts
#      are P3's; the roster total across the lane is derived in phase4-plan.md)
ls agents/*/skills-template/injection-screen.md | wc -l    # expected 4 (canonical + 3 mirrors)

# --- DERIVATION 2: byte-identity of every mirror (the drift pin, provable by hand) ---
CANON=agents/canary/skills-template/injection-screen.md
for a in hacker cracker sweeper worker; do
  cmp -s "$CANON" "agents/$a/skills-template/injection-screen.md" \
    && echo "OK  $a" || echo "DRIFT $a"
done

# --- DERIVATION 3: the manifest roster (each receiving agent exactly one entry; nobody else) ---
grep -l 'injection-screen' agents/*/skill-set.yaml | sed 's#agents/##;s#/skill-set.yaml##' | sort
#   expected exactly: canary, cracker, hacker, sweeper, worker
grep -c 'name: injection-screen' agents/*/skill-set.yaml | grep -v ':0$'

# --- DERIVATION 4: the propagation lever (equal versions across the roster) ---
grep -A2 'name: injection-screen' agents/*/skill-set.yaml | grep 'version:'
#   every version string MUST be equal — the W4 guard re-seeds only on a STRICTLY higher version,
#   so a same-version byte change would NEVER propagate (the drift trap).

# --- DERIVATION 5: the include guard (AM-7 pattern) ---
grep -n 'include:' agents/*/skills-template/injection-screen.md   # MUST print nothing

# --- DERIVATION 6: the corpus ---
find tests -path '*injection*corpus*' -o -name '*injection_corpus*' | sort
#   expected: one positive fixture per rule ID (INJ-01..INJ-05+) + benign controls per family

# --- DERIVATION 7: the secret-shape scan over the corpus (a needle needs BOTH controls) ---
#   positive control: a fixture that DOES contain a key-shaped string -> the scanner MUST flag it
#   negative control: the real corpus                       -> the scanner MUST NOT flag it
#   (a control-less scanner is vacuous, not clean)
```

---

## Coupling

| Direction | Coupling | Notes |
|---|---|---|
| **in** | **P0** (tight) | the guide checklist, the seeding decision record, the fence precedent, the intake-surface list, the D2/D4 adopted rulings. |
| **out** | **P2** (tight) | the doctrine prose **quotes the grammar by name** — the two rules reference the fence markers and the header fields. If the grammar moves, the doctrine prose moves with it. |
| **out** | **P3** (tight) | canary's copy is the **canonical** body; canary's `tools_note.md` declares that home (guide §2). |
| **out** | **P4** (tight) | P4 pins the roster, the byte-identity, the manifest entries and the version equality; P4 assembles the fence-contract legs authored here into the lane test file. |
| **out** | **P5** | loose — Gate E checks the skill **loads** for every roster member; Gate H checks the corpus carries no real credential material. |
| **out** | **P6** | independent. |

**Freeze point:** the grammar is frozen when P2's wording pass completes — the doctrine prose and the
skill body must not describe two different fences.
