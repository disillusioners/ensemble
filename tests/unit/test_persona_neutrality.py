"""PERMANENT guard: the shipped trees carry no persona-lore TITLE tokens.

Why this file exists
--------------------
The persona seed and the shipped surface were neutralized (lane
``fix/neutral-persona-seed``): the Memoir actor-claim contract keeps three
lore words *only* as an enumerated CLAIM (a value a human types), never as a
derivation and never as prose. That neutralization is easy to undo by
accident — a docstring, a help string, a comment, a new fixture that "looks
like the others" — so it is pinned here instead of trusted.

What this guard pins
--------------------
* The scan is DERIVED AT TEST TIME from the tree with ``git grep`` over the
  shipped trees only (``SCANNED_TREES``). Nothing here hand-transcribes a
  list of hits: a line shift inside an allowlisted file must NOT false-red,
  a NEW token in an allowlisted file DOES red, and ANY token in a
  non-allowlisted file DOES red.
* ``.agents/`` is deliberately OUT of scope: it is the historical
  ruling-trail corpus (plans, handoffs, test results) whose job is to record
  what was decided, verbatim. Sweeping history would destroy the trail; the
  fence is the *shipped* surface.
* The allowlist is keyed by FILE + TOKEN with one reason per entry, so an
  allowed file does not get a blanket licence for a different token.
* The scan FAILS LOUDLY when the scan itself breaks: ``git grep`` rc 0 means
  hits were produced, rc 1 means no hits at all (a VACUOUS green — this
  guard refuses it), and rc >= 128 / negative (not a git tree, broken object
  store, bad pathspec) must never masquerade as "clean".
* This file carries NO literal lore token — the tokens are assembled from
  fragments — so the guard is not a hit in its own scan. That is asserted
  too: a guard that allowlists itself is a guard that can rot.

Scope note: ``git grep`` searches TRACKED files. That is exactly the surface
this guard certifies — the COMMITTED shipped tree. A brand-new file becomes
visible on its first commit, so the review event is the commit, not the
local write.

Maintenance rule
----------------
A new hit is a REVIEW EVENT, not a test-fix event. If a token is genuinely
required (an API contract value, a CHECK mirror, a negative pin), add the
FILE + TOKEN entry below WITH THE REASON. Never widen a file to all tokens,
never add ``.agents/`` to the scan, and never delete the non-vacuity
assertion to make it green.

An allowlist entry that stops being used is harmless: cleaning lore words OUT
of a file is never a regression, so stale entries do not red. Only ADDITIONS
do.
"""

from __future__ import annotations

import functools
import pathlib
import re
import subprocess
from typing import NamedTuple

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

# ── the tokens, assembled from fragments ──────────────────────────────────
# A literal token in THIS file would be a hit in this file's own scan (and
# there is intentionally no allowlist entry for the guard itself). Keep the
# fragments apart; do not "simplify" them into literals.
_LORE_EMPRESS = "emp" + "ress"
_LORE_EMPEROR = "emp" + "er" + "or"
_LORE_EMPYREA = "emp" + "yrea"

LORE_TOKENS: tuple[str, ...] = (_LORE_EMPRESS, _LORE_EMPEROR, _LORE_EMPYREA)

# Word-boundary + case-insensitive, mirroring ``git grep -wIi``: the git scan
# and this side's self-scan must agree on what a hit IS.
TOKEN_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(token) for token in LORE_TOKENS) + r")\b",
    re.IGNORECASE,
)

# ── the scanned surface: SHIPPED trees only ───────────────────────────────
# NOT ``.agents/**`` — that is the historical ruling-trail corpus.
SCANNED_TREES: tuple[str, ...] = (
    "daemon/",
    "tests/",
    "docs/guidelines/",
    "scripts/",
    "agents/",
    "CHANGELOG.md",
)

# ── the allowlist: FILE -> {TOKEN: why it is allowed} ─────────────────────
# Keyed by FILE + TOKEN on purpose: a line-number key would false-red on any
# edit above the hit, and a file-only key would let a SECOND token slip in
# under an existing entry's reason.
ALLOWED: dict[str, dict[str, str]] = {
    "agents/ari/tools_note.md": {
        _LORE_EMPRESS: (
            "actor-claim CONTRACT line: the actor is a CLAIM, never a derivation"
        ),
        _LORE_EMPEROR: (
            "actor-claim CONTRACT line: the actor is a CLAIM, never a derivation"
        ),
    },
    "daemon/migrations/versions/20261105_102821_create_memoir_tables.sql": {
        _LORE_EMPRESS: (
            "pinned_by CHECK mirror in the migration (the applied schema contract)"
        ),
        _LORE_EMPEROR: (
            "pinned_by CHECK mirror in the migration (the applied schema contract)"
        ),
    },
    "daemon/repositories/memoir/models.py": {
        _LORE_EMPRESS: "PIN_ACTORS enum + pinned_by CHECK mirror (schema contract)",
        _LORE_EMPEROR: "PIN_ACTORS enum + pinned_by CHECK mirror (schema contract)",
    },
    "daemon/tools/memoir.py": {
        _LORE_EMPRESS: "actor-claim help / refusal text shown to the caller",
        _LORE_EMPEROR: "actor-claim help / refusal text shown to the caller",
    },
    "tests/postgres/test_memoir_pg_corpus.py": {
        _LORE_EMPEROR: "fixture boundary value for the pinned_by CHECK (an actor)",
    },
    "tests/unit/test_memoir_admission_unit.py": {
        _LORE_EMPRESS: "admission fixtures exercising the enumerated actor claim",
        _LORE_EMPEROR: "admission fixtures exercising the enumerated actor claim",
    },
    "tests/unit/test_memoir_briefing_seam.py": {
        _LORE_EMPEROR: "fixture actor value for the briefing seam row",
    },
    "tests/unit/test_memoir_briefing_unit.py": {
        _LORE_EMPRESS: "briefing fixtures exercising both enumerated actor values",
        _LORE_EMPEROR: "briefing fixtures exercising both enumerated actor values",
    },
    "tests/unit/test_memoir_keepsake_proposals.py": {
        _LORE_EMPRESS: "keepsake-proposal fixture pinned by the enumerated actor claim",
    },
    "tests/unit/test_memoir_persona_seed.py": {
        _LORE_EMPYREA: (
            "NEGATIVE pin: 'the lore title is NOT in the seed' — the sweep's proof"
        ),
        _LORE_EMPEROR: (
            "NEGATIVE pin: 'the lore title is NOT in the seed' — the sweep's proof"
        ),
    },
    "tests/unit/test_memoir_retrieval_unit.py": {
        _LORE_EMPRESS: "retrieval fixture pinned by the enumerated actor claim",
    },
    "tests/unit/test_memoir_tools_unit.py": {
        _LORE_EMPRESS: "tool-surface fixtures for the enumerated actor claim (required)",
        _LORE_EMPEROR: "tool-surface fixtures for the enumerated actor claim (required)",
    },
    "tests/unit/tools/fixtures/module_level_inputs_c_base.json": {
        _LORE_EMPRESS: (
            "the byte-identity BASE fixture — the captured memoir actor-field "
            "schema contract text, mirrored verbatim from daemon/tools/memoir.py"
        ),
        _LORE_EMPEROR: (
            "the byte-identity BASE fixture — the captured memoir actor-field "
            "schema contract text, mirrored verbatim from daemon/tools/memoir.py"
        ),
    },
    "tests/unit/test_memoir_v2_schema.py": {
        _LORE_EMPRESS: "pinned_by CHECK text asserted against the v2 schema (mirror)",
        _LORE_EMPEROR: "pinned_by CHECK text asserted against the v2 schema (mirror)",
    },
    "tests/unit/test_memoir_wake_refresh.py": {
        _LORE_EMPRESS: "wake-refresh fixture pinned by the enumerated actor claim",
    },
    "tests/unit/test_memoir_write_purity_unit.py": {
        _LORE_EMPRESS: "write-purity fixture pinned by the enumerated actor claim",
        _LORE_EMPEROR: "write-purity fixture pinned by the enumerated actor claim",
    },
}

GUARD_PATH = "tests/unit/test_persona_neutrality.py"


class Hit(NamedTuple):
    """One (file, line, token) hit, derived from the tree."""

    path: str
    lineno: int
    token: str


def _run_git_grep() -> subprocess.CompletedProcess[str]:
    """Run ``git grep -nwIi -e <token>...`` over the shipped trees."""
    argv = ["git", "grep", "-nwIi"]
    for token in LORE_TOKENS:
        argv += ["-e", token]
    argv += ["--", *SCANNED_TREES]
    return subprocess.run(
        argv,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )


@functools.lru_cache(maxsize=1)
def derive_hits() -> tuple[Hit, ...]:
    """The hit set, DERIVED from the tree at test time (never transcribed).

    Exit-code discipline (the whole point of a grep-backed pin):

    * 0 — hits were produced (the only acceptable "the scan ran" signal);
    * 1 — NO hits anywhere: a vacuous green, refused as a FAILURE;
    * >= 128 / negative — the scan ITSELF failed (not a git tree, broken
      object store, bad pathspec). Must fail loudly, never pass silently.
    """
    for tree in SCANNED_TREES:
        target = REPO_ROOT / tree
        assert target.exists(), (
            f"scanned tree {tree!r} does not exist under {REPO_ROOT} — the scan "
            "would silently narrow its own surface"
        )

    result = _run_git_grep()
    stderr = (result.stderr or "").strip()
    rc = result.returncode
    assert 0 <= rc <= 1, (
        "the persona-lore SCAN ITSELF failed — this is not a clean result: "
        f"`git grep` rc={rc} (stderr={stderr!r}). rc >= 128 (or negative) means "
        "git could not run the scan: not inside a git tree, a broken object "
        "store, or a bad pathspec. A green here would certify nothing."
    )
    assert rc == 0, (
        "the persona-lore scan produced ZERO hits (rc=1) — a vacuous pin. "
        "Either the lore titles no longer exist anywhere in the shipped trees "
        "(then this guard is obsolete and must be RE-FOUNDED, not deleted "
        f"silently) or the scan stopped reaching the trees it claims to cover "
        f"(stderr={stderr!r})."
    )

    hits: list[Hit] = []
    for raw in result.stdout.splitlines():
        if not raw.strip():
            continue
        path, sep, rest = raw.partition(":")
        lineno_text, sep2, text = rest.partition(":")
        assert sep and sep2 and lineno_text.isdigit(), (
            f"unparseable `git grep -n` line: {raw!r}"
        )
        line_tokens = [match.group(0).lower() for match in TOKEN_RE.finditer(text)]
        # `git grep -w` and Python's ``\b`` disagree only on exotic boundaries
        # (non-ASCII word chars). If git reports a word-boundary hit on a line
        # this side reads as token-free, the two scans have drifted: fail
        # loudly instead of silently narrowing the derived set.
        assert line_tokens, (
            "`git grep -w` matched a line with no token under this file's "
            f"word-boundary regex — the scans have drifted: {raw!r}"
        )
        for token in line_tokens:
            hits.append(Hit(path, int(lineno_text), token))
    return tuple(hits)


def _render(hits: list[Hit] | tuple[Hit, ...]) -> str:
    return "\n".join(f"  {hit.path}:{hit.lineno}: token {hit.token!r}" for hit in hits)


# ── the guard ─────────────────────────────────────────────────────────────


def test_the_shipped_trees_carry_no_persona_lore_titles():
    """Every derived hit must be covered by its FILE + TOKEN allowlist entry."""
    hits = derive_hits()
    # Non-vacuity (belt over the rc check above, because this assertion is the
    # one a future "simplification" is most likely to delete).
    assert hits, (
        "the persona-lore scan returned ZERO hits — a pin that certifies "
        "nothing. Either the tokens no longer exist anywhere (then this guard "
        "is obsolete and must be re-founded, not deleted silently) or the "
        "scan stopped reaching the trees it claims to cover."
    )

    violations = [
        hit
        for hit in hits
        if hit.path not in ALLOWED or hit.token not in ALLOWED[hit.path]
    ]
    if not violations:
        return

    details = []
    for hit in violations:
        per_file = ALLOWED.get(hit.path)
        if per_file is None:
            reason = "FILE is not allowlisted for any lore token"
        else:
            reason = f"only {sorted(per_file)!r} allowed in this FILE"
        details.append(f"  {hit.path}:{hit.lineno}: token {hit.token!r} ({reason})")
    raise AssertionError(
        "persona-lore title token(s) present in the SHIPPED trees — the "
        "neutralized surface regressed. A lore title may exist only as an "
        "enumerated actor CLAIM (API contract / CHECK mirror / negative pin) "
        "and only in an allowlisted FILE for that TOKEN:\n" + "\n".join(details)
    )


def test_the_guard_file_carries_no_literal_token():
    """The guard must be clean in its own scan — it allowlists nothing of itself."""
    own_source = pathlib.Path(__file__).read_text(encoding="utf-8")
    literals = sorted({match.group(0) for match in TOKEN_RE.finditer(own_source)})
    assert not literals, (
        f"this guard file carries literal lore token(s) {literals!r}; assemble "
        'them from fragments (e.g. "emp" + "ress") so the guard cannot be a hit '
        "in its own scan — a self-allowlisted guard can rot unnoticed"
    )
    # The derived scan (which sees this file once it is tracked) must not
    # report it either. While the file is untracked `git grep` cannot see it
    # at all, so the byte-level check above is what holds in that state.
    self_hits = [hit for hit in derive_hits() if hit.path == GUARD_PATH]
    assert not self_hits, "the guard matched its own scan:\n" + _render(self_hits)


def test_the_scan_scope_excludes_the_historical_ruling_trail():
    """``.agents/**`` is the ruling trail, not the shipped surface."""
    offenders = [tree for tree in SCANNED_TREES if tree.lstrip("./").startswith(".agents")]
    assert not offenders, (
        f"the scan was widened to the historical ruling trail: {offenders!r}. "
        ".agents/ records what was decided, verbatim, and must not be swept."
    )
    bad_entries = [path for path in ALLOWED if path.lstrip("./").startswith(".agents/")]
    assert not bad_entries, (
        f"allowlist entries point at the ruling trail: {bad_entries!r}"
    )


def test_the_allowlist_grants_tokens_per_file_not_per_corpus():
    """No blanket entries: every key is a single file, every value a reason."""
    for path, per_token in ALLOWED.items():
        assert path and not path.endswith("/"), (
            f"allowlist key {path!r} is not a single file path (directory and "
            "empty keys would grant a blanket licence)"
        )
        assert per_token, f"allowlist entry {path!r} is empty — delete it instead"
        for token, reason in per_token.items():
            assert token in LORE_TOKENS, (
                f"allowlist entry {path!r} names {token!r}, which is not one of "
                "the pinned lore tokens"
            )
            assert isinstance(reason, str) and reason.strip(), (
                f"allowlist entry {path!r} -> {token!r} carries no WHY comment"
            )
