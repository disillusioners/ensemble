"""AST invariants for the checkpoint read-cache substrate — read-cache PR-1.

House template: ``tests/integration/test_armed_absence_alist.py``'s
``TestRepoWideGrepGuard`` (the §33/FR-7 armed-absence pattern — AST call
scan over ``daemon/**/*.py`` + explicit allowlist + VACUITY self-checks
so the scan can never silently rot into a always-green no-op).

Read-cache plan §4.3.C pins TWO structural invariants:

* **Pattern A — saver write-call ban.** No NEW call site may invoke a
  checkpoint-saver WRITE method on a saver-ish receiver outside the
  sanctioned files. Every write must flow through either (a) the
  retry proxy's write forwarders (``daemon/checkpoint_adapter.py`` —
  the cache wrapper module, whose forwarders invalidate before
  delegating), (b) the ADAPTER surface (``maintenance.py`` calls the
  adapter's ``adelete_thread`` — a raw-SQL method that carries its own
  explicit invalidation hook), (c) the readiness sentinel (its
  ``aput_writes`` targets the dedicated sentinel thread and rides the
  PROXY, so the proxy forwarder invalidates), or (d) the one-shot
  offline migrator (bare saver; never concurrent with live traffic).

* **Pattern B — raw-SQL checkpoint-table mutation ban.** No NEW call
  site may run a raw ``DELETE``/``UPDATE``/``INSERT`` against
  ``checkpoints`` / ``checkpoint_writes`` / ``checkpoint_blobs``
  outside ``daemon/checkpoint_adapter.py`` — the invalidation-routed
  helper module whose four raw-SQL DELETE sites call
  ``invalidate_cached_reads`` before the DELETE (plan §4.3.B).

* **Sentinel thread pin (static companion).** The readiness sentinel's
  write config must be built from
  ``CHECKPOINT_SENTINEL_THREAD_ID`` so its 10 s-cycle invalidations
  can never churn real-instance cache keys (plan §4.3.D).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DAEMON_ROOT = REPO_ROOT / "daemon"

# ── Pattern A — saver write-call ban ────────────────────────────────────────

#: Saver-API write methods (async + sync twins + update_state family) —
#: the mutator census of plan §4.3.A.
WRITE_METHODS: frozenset[str] = frozenset(
    {
        "aput",
        "aput_writes",
        "adelete_thread",
        "adelete_for_runs",
        "acopy_thread",
        "aprune",
        "put",
        "put_writes",
        "delete_thread",
        "delete_for_runs",
        "copy_thread",
        "prune",
        "aupdate_state",
        "adelete_checkpoint",
        "update_state",
        "delete_checkpoint",
    }
)

#: Receivers whose names syntactically resolve to "a saver-ish object".
SAVERISH_RECEIVERS: frozenset[str] = frozenset(
    {
        "raw_saver",
        "saver",
        "_saver",
        "checkpointer",
        "_checkpointer",
        "pg_checkpointer",
        "sqlite_checkpointer",
        "proxy",
    }
)

#: The sanctioned write-call files (see module docstring for the why
#: of each). The WRAPPER is ``daemon/checkpoint_adapter.py`` — the
#: cache wrapper module whose proxy write forwarders invalidate before
#: delegating (plan §4.3.A: "allowlist: the cache wrapper module only"
#: — plus the three pre-existing legitimate surfaces the plan's §4.3.B
#: table itself enumerates: the adapter adelete_thread CALLERS
#: (maintenance.py), the sentinel writer (readiness.py, §4.3.D), and
#: the offline migrator (bare-saver one-shot, never live-concurrent).
PATTERN_A_ALLOWLIST: frozenset[str] = frozenset(
    {
        "daemon/checkpoint_adapter.py",
        "daemon/migrations/checkpoint_migrator.py",
        "daemon/services/maintenance.py",
        "daemon/services/readiness.py",
    }
)

#: The cache WRAPPER module (the plan's "the cache wrapper module
#: ONLY" anchor — every other allowlist entry is a call-site surface,
#: not a wrapper).
CACHE_WRAPPER_MODULE = "daemon/checkpoint_adapter.py"


def _receiver_name(node: ast.Call) -> str | None:
    recv = node.func.value
    if isinstance(recv, ast.Name):
        return recv.id
    if isinstance(recv, ast.Attribute):
        return recv.attr
    return None


def _scan_pattern_a(*, ignore_allowlist: bool = False) -> list[tuple[str, int, str]]:
    """Every saver write-call site in daemon/ outside the allowlist."""
    violations: list[tuple[str, int, str]] = []
    for path in sorted(DAEMON_ROOT.rglob("*.py")):
        relpath = path.relative_to(REPO_ROOT).as_posix()
        if not ignore_allowlist and relpath in PATTERN_A_ALLOWLIST:
            continue
        try:
            source = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in WRITE_METHODS
                and _receiver_name(node) in SAVERISH_RECEIVERS
            ):
                violations.append((relpath, node.lineno, node.func.attr))
    return violations


class TestPatternASaverWriteCallBan:
    def test_zero_saver_write_calls_outside_the_allowlist(self):
        violations = _scan_pattern_a()
        assert violations == [], (
            f"Found {len(violations)} saver write-call site(s) in "
            f"daemon/**/*.py outside the sanctioned files:\n"
            + "\n".join(f"  {p}:{ln} .{attr}()" for p, ln, attr in violations)
            + "\nEvery checkpoint write must flow through the retry proxy's "
            "write forwarders (invalidating) or the adapter surface "
            "(explicitly hooked) — see read-cache plan §4.3."
        )

    # ── vacuity self-checks (else the scan is a no-op green) ────────

    def test_allowlist_entries_all_have_live_write_calls(self):
        """Every allowlist entry contains ≥1 flagged call — no dead entry."""
        live = {relpath for relpath, _ln, _attr in _scan_pattern_a(ignore_allowlist=True)}
        for entry in PATTERN_A_ALLOWLIST:
            assert entry in live, (
                f"Pattern-A allowlist entry {entry} contains NO flagged "
                "saver write call — the allowlist is stale (dead entry)"
            )

    def test_the_scan_finds_calls_when_the_allowlist_is_ignored(self):
        """The scanner itself is armed: with the allowlist disabled it
        finds write calls (the allowlist is load-bearing, not vacuous)."""
        assert _scan_pattern_a(ignore_allowlist=True), (
            "Pattern A found ZERO write calls even with the allowlist "
            "disabled — the scan is vacuous (receiver/method sets rotted)"
        )

    def test_the_wrapper_module_defines_each_hot_write_forwarder(self):
        """The WRAPPER carries the write surface the allowlist names:
        each hot write attr appears as a flagged call or a
        ``_call_with_retry("<name>"`` string in the wrapper module —
        if a forwarder disappears, this fails (the K-guard companion).
        """
        wrapper = DAEMON_ROOT / "checkpoint_adapter.py"
        source = wrapper.read_text(encoding="utf-8")
        tree = ast.parse(source)
        wrapper_attrs = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in WRITE_METHODS
        }
        retry_names = set(re.findall(r'_call_with_retry\(\s*"([a-z_]+)"', source))
        present = wrapper_attrs | retry_names
        for hot in ("aput", "aput_writes", "adelete_thread"):
            assert hot in present, (
                f"the cache wrapper module no longer carries the {hot} "
                "write forwarder — Pattern A's allowlist went stale"
            )


# ── Pattern B — raw-SQL checkpoint-table mutation ban ───────────────────────

_RAW_SQL_MUTATION = re.compile(
    r"^\s*(DELETE\s+FROM|UPDATE|INSERT\s+INTO)\s+"
    r"(checkpoints|checkpoint_writes|checkpoint_blobs)\b",
    re.IGNORECASE,
)

#: The invalidation-routed helper module: ALL raw-SQL mutations of the
#: three checkpoint tables live in the adapter, and the four DELETE
#: sites carry explicit ``invalidate_cached_reads`` hooks (§4.3.B).
PATTERN_B_ALLOWLIST: frozenset[str] = frozenset({"daemon/checkpoint_adapter.py"})

_SQL_EXECUTORS: frozenset[str] = frozenset(
    {"execute", "executemany", "fetch", "fetchrow", "fetchval"}
)


def _sql_text(node: ast.expr) -> str | None:
    """Statically evaluate the SQL first-arg text: plain constants,
    implicit string concatenation, f-string leading literal, and
    ``+``-concatenated chains (the anti-join chunk shape — where the
    LEADING constant carries the ``DELETE FROM checkpoint_blobs``
    head and the tail operands reference module predicates).

    For a ``+`` chain whose later operands are not statically known
    (Name references to module constants), the LEADING evaluable text
    is returned: the mutation regex anchors at ``^``, so the head is
    all the match needs.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr) and node.values:
        first = node.values[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            return first.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _sql_text(node.left)
        if left is not None:
            return left  # the leading text is all the anchored regex needs
        return _sql_text(node.right)
    return None


def _scan_pattern_b(*, ignore_allowlist: bool = False) -> list[tuple[str, int, str]]:
    violations: list[tuple[str, int, str]] = []
    for path in sorted(DAEMON_ROOT.rglob("*.py")):
        relpath = path.relative_to(REPO_ROOT).as_posix()
        if not ignore_allowlist and relpath in PATTERN_B_ALLOWLIST:
            continue
        try:
            source = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in _SQL_EXECUTORS
                and node.args
            ):
                text = _sql_text(node.args[0])
                if text is not None and _RAW_SQL_MUTATION.match(text):
                    violations.append((relpath, node.lineno, text.strip()[:60]))
    return violations


class TestPatternBRawSqlMutationBan:
    def test_zero_raw_sql_checkpoint_mutations_outside_the_helper(self):
        violations = _scan_pattern_b()
        assert violations == [], (
            f"Found {len(violations)} raw-SQL checkpoint-table mutation(s) "
            f"in daemon/**/*.py outside the invalidation-routed helper:\n"
            + "\n".join(f"  {p}:{ln} {text!r}" for p, ln, text in violations)
            + "\nRaw SQL bypasses the proxy — route through the adapter's "
            "hooked methods or add an invalidate_cached_reads seam "
            "(read-cache plan §4.3.B)."
        )

    # ── vacuity self-checks ─────────────────────────────────────────

    def test_the_helper_module_has_live_raw_sql_mutations(self):
        """The allowlisted module really mutates the checkpoint tables
        via raw SQL (7 sites today) — the entry is not dead."""
        live = {relpath for relpath, _ln, _text in _scan_pattern_b(ignore_allowlist=True)}
        for entry in PATTERN_B_ALLOWLIST:
            assert entry in live, (
                f"Pattern-B allowlist entry {entry} contains NO raw-SQL "
                "checkpoint mutation — the allowlist is stale"
            )

    def test_the_regex_matches_a_positive_control(self):
        assert _RAW_SQL_MUTATION.match("DELETE FROM checkpoints WHERE thread_id = $1")
        assert _RAW_SQL_MUTATION.match("delete from checkpoint_blobs b WHERE b.ctid IN (")
        assert _RAW_SQL_MUTATION.match("UPDATE checkpoint_writes SET x = 1")
        assert _RAW_SQL_MUTATION.match("INSERT INTO checkpoints VALUES (1)")
        # Non-mutations must NOT match (SQLite's `writes` table, SELECTs).
        assert not _RAW_SQL_MUTATION.match("DELETE FROM writes WHERE thread_id = ?")
        assert not _RAW_SQL_MUTATION.match("SELECT 1 FROM checkpoints")

    def test_binop_concat_sql_is_still_detected(self):
        """The anti-join chunk shape (constant + module predicate +
        constant) must remain detectable — the C1 review site."""
        src = (
            'rows = await conn.fetch(\n'
            '    "DELETE FROM checkpoint_blobs b WHERE b.ctid IN ("\n'
            '    "SELECT ..." + _PRED + " LIMIT $3"\n'
            '    ") RETURNING ...",\n'
            ')\n'
        )
        tree = ast.parse(src)
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "fetch"
        ]
        assert calls, "control source produced no fetch call"
        text = _sql_text(calls[0].args[0])
        assert text is not None and _RAW_SQL_MUTATION.match(text)


# ── Sentinel thread pin (static companion — plan §4.3.D) ────────────────────


class TestSentinelThreadPin:
    def test_readiness_sentinel_write_targets_the_dedicated_thread(self):
        """AST: the sentinel write config is built from
        CHECKPOINT_SENTINEL_THREAD_ID (never a literal / instance id)."""
        readiness = (DAEMON_ROOT / "services" / "readiness.py").read_text(
            encoding="utf-8"
        )
        tree = ast.parse(readiness)
        # Find dict literals whose 'configurable' dict has a thread_id
        # key; assert the value references the sentinel CONSTANT.
        found = 0
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            for key, value in zip(node.keys, node.values):
                if (
                    isinstance(key, ast.Constant)
                    and key.value == "thread_id"
                    and isinstance(value, ast.Name)
                    and value.id == "CHECKPOINT_SENTINEL_THREAD_ID"
                ):
                    found += 1
        assert found >= 2, (
            "the readiness sentinel configs no longer reference "
            "CHECKPOINT_SENTINEL_THREAD_ID for thread_id — a sentinel "
            "write/invalidation could churn real-instance cache keys "
            "(read-cache plan §4.3.D)"
        )

    def test_the_sentinel_constant_is_the_documented_value(self):
        import sys

        sys.path.insert(0, str(REPO_ROOT))
        from daemon.constants import CHECKPOINT_SENTINEL_THREAD_ID

        assert CHECKPOINT_SENTINEL_THREAD_ID == "__ensemble_readiness_probe__"
