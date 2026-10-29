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
        """The WRAPPER carries the write surface the allowlist names —
        scoped to the ``_SaverRetryProxy`` CLASS BODY (fix pass M4):
        the whole-file scan was vacuous for ``adelete_thread`` (the
        attr it found lived in the SQLite ADAPTER's method, not the
        proxy). Inside the proxy body the hot writes appear either as
        explicit ``self._saver.<attr>(...)`` forwarders or as
        ``_call_with_retry("<name>"`` retry strings; if a forwarder
        disappears, this fails (the K-guard companion)."""
        wrapper = DAEMON_ROOT / "checkpoint_adapter.py"
        source = wrapper.read_text(encoding="utf-8")
        tree = ast.parse(source)
        proxy_class: ast.ClassDef | None = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == (
                "_wrap_saver_with_connection_retry"
            ):
                for sub in ast.walk(node):
                    if isinstance(sub, ast.ClassDef) and sub.name == "_SaverRetryProxy":
                        proxy_class = sub
                        break
        assert proxy_class is not None, (
            "the _SaverRetryProxy class no longer lives inside "
            "_wrap_saver_with_connection_retry — Pattern A's wrapper "
            "anchor rotted"
        )
        proxy_attrs = {
            node.func.attr
            for node in ast.walk(proxy_class)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in WRITE_METHODS
        }
        proxy_source = ast.get_source_segment(source, proxy_class) or ""
        retry_names = set(
            re.findall(r'_call_with_retry\(\s*"([a-z_]+)"', proxy_source)
        )
        present = proxy_attrs | retry_names
        for hot in ("aput", "aput_writes", "adelete_thread"):
            assert hot in present, (
                f"the _SaverRetryProxy class body no longer carries the "
                f"{hot} write forwarder — Pattern A's allowlist went stale"
            )
        # The class-scoped scan is ARMED: the proxy body really does
        # contain explicit write-attr forwarders (not only retry
        # strings) — otherwise the attrs half of this check is vacuous.
        assert {"put", "put_writes", "delete_thread"} <= proxy_attrs, (
            "the proxy's sync-twin write forwarders vanished from the "
            "class body — the attrs scan is vacuous"
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

    def test_production_adapter_raw_sql_census_is_pinned_at_seven(self):
        """Fix pass M3: pin the PRODUCTION site census (not just a
        synthetic control) — ``daemon/checkpoint_adapter.py`` carries
        EXACTLY 7 detectable raw-SQL checkpoint-table mutations, and
        the BinOp-concat chunk site (the anti-join DELETE) is among
        them. Production SQL drift that changes the shape (e.g. the
        leading constant moves behind an opaque expression) now REDs
        here instead of evading with all-green."""
        sites = _scan_pattern_b(ignore_allowlist=True)
        adapter_sites = [
            (ln, text) for p, ln, text in sites if p == "daemon/checkpoint_adapter.py"
        ]
        assert len(adapter_sites) == 7, (
            f"expected the pinned 7 raw-SQL checkpoint mutations in the "
            f"adapter, found {len(adapter_sites)}: {adapter_sites} — the "
            "site census moved (new seam added, or a site's SQL shape "
            "became statically undetectable)"
        )
        texts = [text for _ln, text in adapter_sites]
        # The BinOp chunk site — the one a Constant-only scanner misses.
        assert any(
            t.startswith("DELETE FROM checkpoint_blobs b WHERE b.ctid") for t in texts
        ), "the anti-join chunk DELETE is no longer statically detectable"
        # The distribution across the three tables (SQLite twins use
        # their own `writes` table and are NOT in this census):
        #   checkpoints ×3 (SQLite exclude, PG exclude, PG adelete_thread)
        #   checkpoint_writes ×2 (PG exclude, PG adelete_thread)
        #   checkpoint_blobs ×2 (PG adelete_thread, the anti-join chunk)
        assert sum("DELETE FROM checkpoints" in t for t in texts) == 3
        assert sum("DELETE FROM checkpoint_writes" in t for t in texts) == 2
        assert sum("DELETE FROM checkpoint_blobs" in t for t in texts) == 2


# ── Invalidation-hook existence pin (fix pass M5) ───────────────────────────


def _count_invalidate_calls(method: ast.AsyncFunctionDef | ast.FunctionDef) -> int:
    """Count ``invalidate_cached_reads(...)`` call sites in a method
    body. The helper is imported at module level, so the call is an
    ``ast.Name`` (not an Attribute) — match both shapes defensively."""
    count = 0
    for node in ast.walk(method):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == "invalidate_cached_reads":
            count += 1
        elif (
            isinstance(func, ast.Attribute) and func.attr == "invalidate_cached_reads"
        ):
            count += 1
    return count


class TestInvalidationHooksExist:
    """A deleted invalidation hook previously left ALL 10 AST invariants
    green (only the behavior pack caught it). This pin asserts the
    HOOK CALLS THEMSELVES exist, per seam, in the adapter source:

    * ``PostgresCheckpointerAdapter`` — the 4 raw-SQL seams carry the
      DOUBLE protection (fix pass F4): ≥2 ``invalidate_cached_reads``
      calls each (BEFORE-half + AFTER-half);
    * ``SqliteCheckpointerAdapter`` — the 3 defensive twins carry ≥1
      each (before-only; bare saver, no cache to protect).
    """

    #: (class name, {method: minimum invalidate_cached_reads call count})
    _EXPECTED: dict[str, dict[str, int]] = {
        "PostgresCheckpointerAdapter": {
            "delete_checkpoints_excluding": 2,
            "delete_writes_excluding": 2,
            "adelete_thread": 2,
            "delete_blobs_anti_join": 2,
        },
        "SqliteCheckpointerAdapter": {
            "delete_checkpoints_excluding": 1,
            "delete_writes_excluding": 1,
            "adelete_thread": 1,
        },
    }

    def _class_defs(self):
        adapter = (DAEMON_ROOT / "checkpoint_adapter.py").read_text(encoding="utf-8")
        tree = ast.parse(adapter)
        classes = {
            node.name: node
            for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef)
        }
        return adapter, classes

    def test_every_seam_carries_its_pinned_hook_count(self):
        _source, classes = self._class_defs()
        for class_name, methods in self._EXPECTED.items():
            cls = classes.get(class_name)
            assert cls is not None, f"{class_name} vanished from checkpoint_adapter.py"
            found: dict[str, int] = {}
            for node in ast.walk(cls):
                if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
                    if node.name in methods:
                        found[node.name] = _count_invalidate_calls(node)
            assert set(found) == set(methods), (
                f"{class_name}: expected methods {sorted(methods)}, "
                f"found {sorted(found)} — a seam method was renamed/removed"
            )
            for method_name, minimum in methods.items():
                assert found[method_name] >= minimum, (
                    f"{class_name}.{method_name} carries only "
                    f"{found[method_name]} invalidate_cached_reads call(s) "
                    f"(pinned minimum {minimum}) — an invalidation hook "
                    "was deleted; the raw-SQL bypass hole is open"
                )

    def test_the_pin_is_armed_against_the_current_tree(self):
        """Vacuity guard: the current tree must actually satisfy the
        census with the exact totals (4 PG seams × 2 + 3 SQLite × 1 =
        11 adapter hook calls) — a pin that can never fire is dead."""
        _source, classes = self._class_defs()
        total = sum(
            _count_invalidate_calls(node)
            for cls_name in self._EXPECTED
            for node in ast.walk(classes[cls_name])
            if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef))
            and node.name in self._EXPECTED[cls_name]
        )
        expected_total = sum(sum(m.values()) for m in self._EXPECTED.values())
        assert expected_total == 11  # the pinned census itself is sane
        assert total == expected_total, (
            f"adapter carries {total} hook calls, the pin expects "
            f"{expected_total} — update the pin DELIBERATELY when a seam "
            "count changes (never silently)"
        )


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
