```
IMPORT-PROOF impl=cpython daemon=/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble-wt-critical-notes-cap-fix/daemon/__init__.py tree_root=/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble-wt-critical-notes-cap-fix
IMPORT-PROOF impl=cpython daemon=/private/tmp/cncap-base-78f1aaead/daemon/__init__.py tree_root=/tmp/cncap-base-78f1aaead
```

macOS `/tmp` → `/private/tmp` realpath handled on BOTH sides of the guard (resolved compare).

## Observed matrix (OBSERVED — script was not tuned to force it)

Probe semantics: `listed` = byte-exact presence in `list_critical_notes()`; `get_canonical` /
`remove_canonical` = `get/remove_critical_note(project_id, CANONICAL)`; `remove_variant` =
`remove_critical_note(project_id, STORED BYTES)` on a SEPARATE fresh DB; `row_gone(c)` /
`row_gone(v)` = direct id-column table scan after the respective remove.

| Variant (stored bytes) | Leg | listed | get_canonical | remove_canonical | row_gone(c) | remove_variant | row_gone(v) |
|---|---|---|---|---|---|---|---|
| v1 UPPERCASE | BASE | true | **false** | **false** | **false (row stays)** | true | true |
| v1 UPPERCASE | HEAD | true | true | **true** | true | true | true |
| v2 TRAILING SPACE | BASE | true | **false** | **false** | **false (row stays)** | true | true |
| v2 TRAILING SPACE | HEAD | true | true | **true** | true | true | true |
| v3 U+200B ZWSP PREFIX | BASE | true | **false** | **false** | **false (row stays)** | true | true |
| v3 U+200B ZWSP PREFIX | HEAD | true | true | **true** | true | true | true |
| v4 UN-HYPHENATED 32-hex | BASE | true | **false** | **false** | **false (row stays)** | true | true |
| v4 UN-HYPHENATED 32-hex | HEAD | true | true | **true** | true | true | true |
| v5 MALFORMED 5-char 4th group | BASE | true | false | false | false (row stays) | true | true |
| v5 MALFORMED 5-char 4th group | HEAD | true | false | false | false (row stays) | true | true |
| v6 CANONICAL control | BASE | true | true | true | true | true | true |
| v6 CANONICAL control | HEAD | true | true | true | true | true | true |

**Verdicts**
- **v1–v4: BASE defect REPRODUCED** (canonical form cannot address the row — `remove_canonical=false`,
  row stays; `get_canonical` also None) and **CLOSED at HEAD** (representation resolver matches:
  `remove_canonical=true`, `row_gone=true`). v1 detail: HEAD's resolver returns the STORED bytes
  (`'D0371008-…'`) — it never rewrites a stored id.
- **v5: identical on BOTH legs** — not resolvable via canonical form (`remove_canonical=false`,
  row stays on both). This is the ASSERTED LIMITATION at HEAD (W1: `uuid.UUID` rejects the shape,
  fingerprint arm requires exactly 32 hex on both sides) and plain exact-PK behavior at BASE.
  Honest extra observation, both legs: the row IS removable via its EXACT stored malformed bytes
  (`remove_variant=true`) — the limitation is representation-matching, not addressability.
- **v6 control: removable on both legs** — harness works at BASE (no base-leg artifact).

## Harness notes (method, not results)
- Seeding bypasses add-time id normalization: model constructed WITHOUT an `id` kwarg
  (throwaway default_factory id), variant bytes assigned to `note.id` POST-construction; a raw
  re-read then asserts the stored bytes survived (`stored_bytes_ok=true` on every seed, both legs).
  Necessary at HEAD: `CriticalNoteModel.__init__` normalizes `id` kwargs (the fix's write-side seam).
- v5 canonical probe input = closest VALID uuid (stored bytes with the extra hex char dropped from
  the 4th group) — no true canonical form exists; position of the extra char unknowable by
  construction. Documented, not tuned.
- Fresh in-memory DB per probe (even separate seeds for canonical vs variant remove) — zero
  cross-contamination.
- Per-probe repository exceptions are recorded verbatim in the JSON, never fatal; all cells below
  are `null`-error (clean).

## HEAD leg — full log (`/tmp/cncap_ab_head.log`, rc=0)

```
IMPORT-PROOF impl=cpython daemon=/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble-wt-critical-notes-cap-fix/daemon/__init__.py tree_root=/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble-wt-critical-notes-cap-fix
HARNESS-SHA256 0a18b9cc900e81e3a1ae721984b9713bdfaa4d8b37ab4b3855cbef4fb9d2574c path=/private/tmp/cncap_ab_harness.py
LEG tree_root=/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble-wt-critical-notes-cap-fix
{"canonical": "d0371008-a55d-4b86-831d-1c76b381a920", "get_canonical_found": true, "get_canonical_returned_id": "'D0371008-A55D-4B86-831D-1C76B381A920'", "listed": true, "listed_error": null, "remove_canonical": true, "remove_canonical_error": null, "remove_variant": true, "remove_variant_error": null, "row_gone_after_canonical": true, "row_gone_after_canonical_error": null, "row_gone_after_variant": true, "row_gone_after_variant_error": null, "stored_bytes_ok": true, "stored_bytes_ok_seed_b": true, "stored_repr": "'D0371008-A55D-4B86-831D-1C76B381A920'", "variant": "v1_uppercase"}
{"canonical": "d0371008-a55d-4b86-831d-1c76b381a920", "get_canonical_found": true, "get_canonical_returned_id": "'d0371008-a55d-4b86-831d-1c76b381a920 '", "listed": true, "listed_error": null, "remove_canonical": true, "remove_canonical_error": null, "remove_variant": true, "remove_variant_error": null, "row_gone_after_canonical": true, "row_gone_after_canonical_error": null, "row_gone_after_variant": true, "row_gone_after_variant_error": null, "stored_bytes_ok": true, "stored_bytes_ok_seed_b": true, "stored_repr": "'d0371008-a55d-4b86-831d-1c76b381a920 '", "variant": "v2_trailing_space"}
{"canonical": "d0371008-a55d-4b86-831d-1c76b381a920", "get_canonical_found": true, "get_canonical_returned_id": "'\\u200bd0371008-a55d-4b86-831d-1c76b381a920'", "listed": true, "listed_error": null, "remove_canonical": true, "remove_canonical_error": null, "remove_variant": true, "remove_variant_error": null, "row_gone_after_canonical": true, "row_gone_after_canonical_error": null, "row_gone_after_variant": true, "row_gone_after_variant_error": null, "stored_bytes_ok": true, "stored_bytes_ok_seed_b": true, "stored_repr": "'\\u200bd0371008-a55d-4b86-831d-1c76b381a920'", "variant": "v3_zwsp_prefix"}
{"canonical": "d0371008-a55d-4b86-831d-1c76b381a920", "get_canonical_found": true, "get_canonical_returned_id": "'d0371008a55d4b86831d1c76b381a920'", "listed": true, "listed_error": null, "remove_canonical": true, "remove_canonical_error": null, "remove_variant": true, "remove_variant_error": null, "row_gone_after_canonical": true, "row_gone_after_canonical_error": null, "row_gone_after_variant": true, "row_gone_after_variant_error": null, "stored_bytes_ok": true, "stored_bytes_ok_seed_b": true, "stored_repr": "'d0371008a55d4b86831d1c76b381a920'", "variant": "v4_unhyphenated_32hex"}
{"canonical": "d0371008-a55d-4b86-831d-1c76b381a920", "get_canonical_found": false, "listed": true, "listed_error": null, "remove_canonical": false, "remove_canonical_error": null, "remove_variant": true, "remove_variant_error": null, "row_gone_after_canonical": false, "row_gone_after_canonical_error": null, "row_gone_after_variant": true, "row_gone_after_variant_error": null, "stored_bytes_ok": true, "stored_bytes_ok_seed_b": true, "stored_repr": "'d0371008-a55d-4b86-831d5-1c76b381a920'", "variant": "v5_malformed_5char_group4"}
{"canonical": "d0371008-a55d-4b86-831d-1c76b381a920", "get_canonical_found": true, "get_canonical_returned_id": "'d0371008-a55d-4b86-831d-1c76b381a920'", "listed": true, "listed_error": null, "remove_canonical": true, "remove_canonical_error": null, "remove_variant": true, "remove_variant_error": null, "row_gone_after_canonical": true, "row_gone_after_canonical_error": null, "row_gone_after_variant": true, "row_gone_after_variant_error": null, "stored_bytes_ok": true, "stored_bytes_ok_seed_b": true, "stored_repr": "'d0371008-a55d-4b86-831d-1c76b381a920'", "variant": "v6_canonical_control"}
HARNESS-COMPLETE variants=6
```

## BASE leg — full log (`/tmp/cncap_ab_base.log`, rc=0)
```
IMPORT-PROOF impl=cpython daemon=/private/tmp/cncap-base-78f1aaead/daemon/__init__.py tree_root=/tmp/cncap-base-78f1aaead
HARNESS-SHA256 0a18b9cc900e81e3a1ae721984b9713bdfaa4d8b37ab4b3855cbef4fb9d2574c path=/private/tmp/cncap_ab_harness.py
LEG tree_root=/tmp/cncap-base-78f1aaead
{"canonical": "d0371008-a55d-4b86-831d-1c76b381a920", "get_canonical_found": false, "listed": true, "listed_error": null, "remove_canonical": false, "remove_canonical_error": null, "remove_variant": true, "remove_variant_error": null, "row_gone_after_canonical": false, "row_gone_after_canonical_error": null, "row_gone_after_variant": true, "row_gone_after_variant_error": null, "stored_bytes_ok": true, "stored_bytes_ok_seed_b": true, "stored_repr": "'D0371008-A55D-4B86-831D-1C76B381A920'", "variant": "v1_uppercase"}
{"canonical": "d0371008-a55d-4b86-831d-1c76b381a920", "get_canonical_found": false, "listed": true, "listed_error": null, "remove_canonical": false, "remove_canonical_error": null, "remove_variant": true, "remove_variant_error": null, "row_gone_after_canonical": false, "row_gone_after_canonical_error": null, "row_gone_after_variant": true, "row_gone_after_variant_error": null, "stored_bytes_ok": true, "stored_bytes_ok_seed_b": true, "stored_repr": "'d0371008-a55d-4b86-831d-1c76b381a920 '", "variant": "v2_trailing_space"}
{"canonical": "d0371008-a55d-4b86-831d-1c76b381a920", "get_canonical_found": false, "listed": true, "listed_error": null, "remove_canonical": false, "remove_canonical_error": null, "remove_variant": true, "remove_variant_error": null, "row_gone_after_canonical": false, "row_gone_after_canonical_error": null, "row_gone_after_variant": true, "row_gone_after_variant_error": null, "stored_bytes_ok": true, "stored_bytes_ok_seed_b": true, "stored_repr": "'\\u200bd0371008-a55d-4b86-831d-1c76b381a920'", "variant": "v3_zwsp_prefix"}
{"canonical": "d0371008-a55d-4b86-831d-1c76b381a920", "get_canonical_found": false, "listed": true, "listed_error": null, "remove_canonical": false, "remove_canonical_error": null, "remove_variant": true, "remove_variant_error": null, "row_gone_after_canonical": false, "row_gone_after_canonical_error": null, "row_gone_after_variant": true, "row_gone_after_variant_error": null, "stored_bytes_ok": true, "stored_bytes_ok_seed_b": true, "stored_repr": "'d0371008a55d4b86831d1c76b381a920'", "variant": "v4_unhyphenated_32hex"}
{"canonical": "d0371008-a55d-4b86-831d-1c76b381a920", "get_canonical_found": false, "listed": true, "listed_error": null, "remove_canonical": false, "remove_canonical_error": null, "remove_variant": true, "remove_variant_error": null, "row_gone_after_canonical": false, "row_gone_after_canonical_error": null, "row_gone_after_variant": true, "row_gone_after_variant_error": null, "stored_bytes_ok": true, "stored_bytes_ok_seed_b": true, "stored_repr": "'d0371008-a55d-4b86-831d5-1c76b381a920'", "variant": "v5_malformed_5char_group4"}
{"canonical": "d0371008-a55d-4b86-831d-1c76b381a920", "get_canonical_found": true, "get_canonical_returned_id": "'d0371008-a55d-4b86-831d-1c76b381a920'", "listed": true, "listed_error": null, "remove_canonical": true, "remove_canonical_error": null, "remove_variant": true, "remove_variant_error": null, "row_gone_after_canonical": true, "row_gone_after_canonical_error": null, "row_gone_after_variant": true, "row_gone_after_variant_error": null, "stored_bytes_ok": true, "stored_bytes_ok_seed_b": true, "stored_repr": "'d0371008-a55d-4b86-831d-1c76b381a920'", "variant": "v6_canonical_control"}
HARNESS-COMPLETE variants=6
```

## Cleanup proof

- `git -C <wt> worktree remove --force /tmp/cncap-base-78f1aaead` → OK; `worktree prune` → OK.
- `/tmp/cncap-base-78f1aaead` no longer exists (`ls` → No such file or directory).
- `git worktree list` no longer contains the base worktree.
- TIP wt HEAD unchanged: `c8252f916`; branch `feature/critical-notes-cap-fix`.
- No `__pycache__` writes into the TIP tree by the harness runs (`PYTHONDONTWRITEBYTECODE=1`;
  mtime sweep over `daemon/repositories/project/` found nothing newer than the harness file).

## Worktree hygiene — `git status --porcelain` BEFORE vs AFTER the whole task

BEFORE (captured pre-run):
```
?? .agents/tester/EVIDENCE/2026-10-31-cn-capfix-delta-map.md
?? .agents/tester/EVIDENCE/cn-capfix-gate-ledger.md
```

AFTER (captured post-cleanup, pre-evidence-write):
```
?? .agents/tester/EVIDENCE/2026-10-31-cn-capfix-delta-map.md
?? .agents/tester/EVIDENCE/cn-capfix-gate-ledger.md
```

`diff BEFORE AFTER` → byte-identical (exit 0). The two listed untracked files belong to a
concurrent lane; the only file THIS task adds to the worktree is THIS evidence file
(sanctioned exception). No commits; no other tree edits.
