"""Boot-record pack (T32 / AC45 / AC46 / A8 / A18 / F-03 / F-04).

WHAT THIS PACK PROVES
---------------------
* a boot record is written on **every** boot and carries the effective
  mode, the boot epoch and BOTH passes' ``ContinueResult`` counters;
* the **OFF early-return branch** of BOTH passes (main + orphan sweep)
  records ``off`` **and** emits its existing ``[BOOT_CONTINUE]`` line
  carrying ``mode=off``;
* the pre-pass **intent record** survives a pass that RAISES and reads
  ``effective_mode == "unknown"`` (it can never carry a fabricated mode —
  F-04: the pass reads the switch AFTER the intent write);
* the mode vocabulary is exactly ``{on, off, unknown}`` — an unknown
  string is recorded as ``unknown``, never passed through;
* **AC46 / A18's HYBRID**:
  (i) **STATIC** — the number of ``[BOOT_CONTINUE]`` logger call SITES in
      ``daemon/services/auto_continue_boot_pass.py`` is UNCHANGED vs the
      base revision ``639b7129b`` (a source-level comparison against the
      base commit — reproducible, no live counting);
  (ii) **RUNTIME, positive set assertion** — the set of
      ``[BOOT_CONTINUE]`` call sites carrying ``mode=`` IS exactly the
      five named sites: no sixth, none missing;
* the boot-records directory has its OWN 50-file cap (F-05) and is the
  effective-mode carrier the status surface and the capture manifest read.

SAFETY FENCE: the record root is a ``tmp_path``; the only repository
reads are source reads. No live-shaped path is resolved.
"""

from __future__ import annotations

import ast
import re
import subprocess
from datetime import datetime
from pathlib import Path

import pytest

from daemon.services import auto_continue_boot_pass as boot_pass
from daemon.services.wedge_capture import boot_record as record_module
from daemon.services.wedge_capture.boot_record import (
    BOOT_RECORDS_DIRNAME,
    BOOT_RECORDS_MAX_FILES,
    MODE_OFF,
    MODE_ON,
    MODE_UNKNOWN,
    MODES,
    boot_records_dir,
    epoch_filename,
    read_boot_record,
    read_latest_boot_record,
    result_to_dict,
    write_boot_record,
    write_intent_record,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
BOOT_PASS_FILE = REPO_ROOT / "daemon" / "services" / "auto_continue_boot_pass.py"

#: The base revision this branch diverged from (the SC-9/AC46 static
#: baseline). A source-level comparison against it is reproducible; a
#: live pre-change count is not.
BASE_REVISION = "639b7129b"

EPOCH = datetime(2026, 10, 22, 11, 0, 0)


# ── the record itself ─────────────────────────────────────────────────────


class TestRecordIsWrittenOnEveryBoot:
    def test_the_record_lands_under_the_capture_root(self, tmp_path: Path) -> None:
        path = write_boot_record(
            tmp_path,
            EPOCH,
            effective_mode=MODE_ON,
            raw_env_value="1",
            passes={"autocontinue": {"scheduled": 2}, "orphan_sweep": {"scheduled": 0}},
        )
        assert path is not None
        assert path == (
            boot_records_dir(tmp_path) / f"{epoch_filename(EPOCH)}.json"
        )
        assert path.parent.name == BOOT_RECORDS_DIRNAME
        assert path.exists()

    def test_the_record_carries_the_mode_the_epoch_and_both_passes(
        self, tmp_path: Path
    ) -> None:
        write_boot_record(
            tmp_path,
            EPOCH,
            effective_mode=MODE_ON,
            raw_env_value="1",
            passes={"autocontinue": {"scheduled": 3}, "orphan_sweep": {"scheduled": 1}},
        )
        record = read_boot_record(tmp_path, EPOCH)
        assert record is not None
        assert record["effective_mode"] == MODE_ON
        assert record["raw_env_value"] == "1"
        assert record["boot_epoch"].endswith("Z")
        assert record["passes"]["autocontinue"]["scheduled"] == 3
        assert record["passes"]["orphan_sweep"]["scheduled"] == 1
        assert record["pass_present"] is True

    def test_the_second_pass_is_a_read_modify_write_of_the_same_file(
        self, tmp_path: Path
    ) -> None:
        write_boot_record(
            tmp_path, EPOCH, effective_mode=MODE_ON,
            passes={"autocontinue": {"scheduled": 2}},
        )
        write_boot_record(
            tmp_path, EPOCH, effective_mode=MODE_ON,
            passes={"orphan_sweep": {"scheduled": 4}},
        )
        record = read_boot_record(tmp_path, EPOCH)
        assert record["passes"]["autocontinue"]["scheduled"] == 2
        assert record["passes"]["orphan_sweep"]["scheduled"] == 4

    def test_an_out_of_vocabulary_mode_is_recorded_as_unknown(
        self, tmp_path: Path
    ) -> None:
        write_boot_record(tmp_path, EPOCH, effective_mode="yes")
        record = read_boot_record(tmp_path, EPOCH)
        assert record["effective_mode"] == MODE_UNKNOWN
        assert MODES == frozenset({MODE_ON, MODE_OFF, MODE_UNKNOWN})

    def test_the_mode_semantics_are_recorded_verbatim(self, tmp_path: Path) -> None:
        """RK-29: ``=false`` / ``=off`` / ``=no`` all mean ON — the
        record must document that, or an operator reads a surprise."""
        write_boot_record(tmp_path, EPOCH, effective_mode=MODE_ON, raw_env_value="false")
        record = read_boot_record(tmp_path, EPOCH)
        assert record["raw_env_value"] == "false"
        assert record["effective_mode"] == MODE_ON
        assert 'literal-"0"-only disable' in record["mode_semantics"]

    def test_the_epoch_filename_is_colon_free(self) -> None:
        assert epoch_filename(EPOCH) == "20261022T110000000000Z"
        assert ":" not in epoch_filename(EPOCH)
        assert epoch_filename(None) == "unknown-epoch"


class TestIntentRecordSurvivesARaisingPass:
    def test_the_intent_record_reads_unknown_never_a_fabricated_mode(
        self, tmp_path: Path
    ) -> None:
        path = write_intent_record(tmp_path, EPOCH)
        assert path is not None
        record = read_boot_record(tmp_path, EPOCH)
        assert record["effective_mode"] == MODE_UNKNOWN
        assert record["pass_present"] is True

    def test_the_pass_result_overwrites_the_intent_record(
        self, tmp_path: Path
    ) -> None:
        write_intent_record(tmp_path, EPOCH)
        assert read_boot_record(tmp_path, EPOCH)["effective_mode"] == MODE_UNKNOWN
        write_boot_record(tmp_path, EPOCH, effective_mode=MODE_OFF, raw_env_value="0")
        assert read_boot_record(tmp_path, EPOCH)["effective_mode"] == MODE_OFF


class TestBootRecordsCap:
    def test_the_boot_records_have_their_own_cap(self, tmp_path: Path) -> None:
        """F-05: the mode carrier is NOT pruned with the captures and
        keeps 50 files, newest retained."""
        for index in range(BOOT_RECORDS_MAX_FILES + 5):
            write_boot_record(
                tmp_path,
                datetime(2026, 10, 22, 11, 0, index % 60, index),
                effective_mode=MODE_ON,
            )
        files = sorted(boot_records_dir(tmp_path).glob("*.json"))
        assert len(files) == BOOT_RECORDS_MAX_FILES

    def test_the_newest_record_is_the_one_the_surface_reads(
        self, tmp_path: Path
    ) -> None:
        write_boot_record(tmp_path, datetime(2026, 10, 22, 10, 0, 0), effective_mode=MODE_OFF)
        write_boot_record(tmp_path, datetime(2026, 10, 22, 11, 0, 0), effective_mode=MODE_ON)
        latest = read_latest_boot_record(tmp_path)
        assert latest is not None
        assert latest["effective_mode"] == MODE_ON

    def test_no_records_yields_none(self, tmp_path: Path) -> None:
        assert read_latest_boot_record(tmp_path) is None


# ── AC45 / AC14: the OFF branch, driven for real ──────────────────────────


@pytest.fixture()
def kill_switch_off(monkeypatch: pytest.MonkeyPatch):
    """Set the AutoContinue kill-switch for THIS test only.

    The env var is set through ``monkeypatch`` (auto-restored) and is
    NEVER written into any file — BC-1 forbids persisting a disable.
    """
    monkeypatch.setenv(boot_pass.AUTO_CONTINUE_KILL_SWITCH_ENV, "0")
    yield


class TestOffBranchRuntime:
    def test_the_main_pass_records_and_logs_off(
        self, tmp_path: Path, kill_switch_off, caplog: pytest.LogCaptureFixture
    ) -> None:
        import asyncio

        with caplog.at_level("INFO"):
            result = asyncio.run(
                boot_pass.continue_running_instances_after_restart(
                    manager=None, boot_epoch=EPOCH
                )
            )

        assert result.auto_continue_mode == MODE_OFF
        assert result.auto_continue_raw_env == "0"
        assert result.skipped_kill_switch == 1
        lines = [
            record.getMessage()
            for record in caplog.records
            if "[BOOT_CONTINUE]" in record.getMessage()
        ]
        assert lines, "the OFF branch must emit its existing [BOOT_CONTINUE] line"
        assert all("mode=off" in line for line in lines), lines

    def test_the_orphan_sweep_records_and_logs_off(
        self, tmp_path: Path, kill_switch_off, caplog: pytest.LogCaptureFixture
    ) -> None:
        import asyncio

        with caplog.at_level("INFO"):
            result = asyncio.run(
                boot_pass.sweep_orphaned_live_turns(manager=None, boot_epoch=EPOCH)
            )

        assert result.auto_continue_mode == MODE_OFF
        assert result.skipped_kill_switch == 1
        lines = [
            record.getMessage()
            for record in caplog.records
            if "[BOOT_CONTINUE]" in record.getMessage()
            and "orphan-sweep" in record.getMessage()
        ]
        assert lines
        assert all("mode=off" in line for line in lines), lines

    def test_the_off_boot_writes_a_record_reading_off(
        self, tmp_path: Path, kill_switch_off
    ) -> None:
        """The api.py shape: intent → main pass → sweep → record."""
        import asyncio

        write_intent_record(tmp_path, EPOCH)
        main_result = asyncio.run(
            boot_pass.continue_running_instances_after_restart(
                manager=None, boot_epoch=EPOCH
            )
        )
        sweep_result = asyncio.run(
            boot_pass.sweep_orphaned_live_turns(manager=None, boot_epoch=EPOCH)
        )
        write_boot_record(
            tmp_path,
            EPOCH,
            effective_mode=main_result.auto_continue_mode,
            raw_env_value=main_result.auto_continue_raw_env,
            passes={
                "autocontinue": result_to_dict(main_result),
                "orphan_sweep": result_to_dict(sweep_result),
            },
        )

        record = read_boot_record(tmp_path, EPOCH)
        assert record["effective_mode"] == MODE_OFF
        assert record["raw_env_value"] == "0"
        assert record["passes"]["autocontinue"]["skipped_kill_switch"] == 1
        assert record["passes"]["orphan_sweep"]["skipped_kill_switch"] == 1


# ── RK-29: the literal-"0"-only semantics (single read, carried) ──────────


@pytest.mark.parametrize("raw", ["1", "", "false", "off", "no", "true"])
def test_any_value_but_the_literal_zero_means_on(
    monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    monkeypatch.setenv(boot_pass.AUTO_CONTINUE_KILL_SWITCH_ENV, raw)
    assert boot_pass._auto_continue_enabled() is True
    # The verbatim value rides along from the SAME read (no second read).
    assert boot_pass._last_auto_continue_raw_value() == raw


def test_the_literal_zero_means_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(boot_pass.AUTO_CONTINUE_KILL_SWITCH_ENV, "0")
    assert boot_pass._auto_continue_enabled() is False


def test_the_result_carrier_fields_default_to_unknown() -> None:
    result = boot_pass.ContinueResult()
    assert result.auto_continue_mode == MODE_UNKNOWN
    assert result.auto_continue_raw_env is None
    payload = result_to_dict(result)
    assert payload["auto_continue_mode"] == MODE_UNKNOWN
    assert "auto_continue_raw_env" in payload


# ── AC46 (i): the STATIC site-count assertion ─────────────────────────────


def _logger_boot_continue_sites(source: str) -> list[tuple[int, bool]]:
    """Every ``logger.<level>(...)`` call whose arguments mention
    ``[BOOT_CONTINUE]`` — returned as ``(line, carries_mode)``.

    A *site* is a logger CALL, not a file line: the multi-line calls are
    counted once, at their opening line.
    """
    sites: list[tuple[int, bool]] = []
    for match in re.finditer(r"logger\.\w+\(", source):
        start = match.start()
        depth = 0
        for index in range(match.end() - 1, len(source)):
            char = source[index]
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    block = source[start:index + 1]
                    if "[BOOT_CONTINUE]" in block:
                        sites.append(
                            (source[:start].count("\n") + 1, "mode=" in block)
                        )
                    break
    return sites


def _base_source() -> str:
    """The boot-pass source at the base revision (the reproducible baseline)."""
    completed = subprocess.run(
        ["git", "show", f"{BASE_REVISION}:daemon/services/auto_continue_boot_pass.py"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        pytest.skip(f"base revision {BASE_REVISION} is unavailable: "
                    f"{completed.stderr.strip()[:200]}")
    return completed.stdout


class TestA18HybridStatic:
    def test_the_site_count_is_unchanged_vs_the_base_revision(self) -> None:
        """SC-9 / AC46 (i) — an ADDED FIELD is not a new log site."""
        base = _logger_boot_continue_sites(_base_source())
        head = _logger_boot_continue_sites(BOOT_PASS_FILE.read_text(encoding="utf-8"))
        assert len(head) == len(base), (
            f"[BOOT_CONTINUE] logger call sites drifted: base={len(base)} "
            f"head={len(head)} — the extension must be ADDITIVE (a field), "
            "never a new site"
        )

    def test_the_base_revision_had_no_mode_field(self) -> None:
        base = _logger_boot_continue_sites(_base_source())
        assert not any(carries for _, carries in base)

    def test_exactly_five_sites_carry_the_mode(self) -> None:
        """AC46 (ii) / F-03 — a POSITIVE SET assertion, not a blanket one.

        The blanket form ("every emitted record carries mode=") was
        unsatisfiable: there are ~20 emit sites and only five are named.
        """
        head = _logger_boot_continue_sites(BOOT_PASS_FILE.read_text(encoding="utf-8"))
        carriers = [line for line, carries in head if carries]
        assert len(carriers) == 5, (
            f"exactly 5 [BOOT_CONTINUE] sites must carry mode=, got {carriers}"
        )

    def test_the_five_carriers_are_the_named_sites(self) -> None:
        """Each named site is identified by its MESSAGE TEXT — a moved
        line number does not break the assertion; a REMOVED site does.

        The mapping is taken from the parsed logger CALLS (never from a
        raw text search): the module docstring quotes these lines too,
        and a prose quote must not be mistaken for a call site.
        """
        source = BOOT_PASS_FILE.read_text(encoding="utf-8")
        expected = {
            "[BOOT_CONTINUE] OFF:": True,                      # main, OFF early return
            "[BOOT_CONTINUE] instance=": True,                 # main, success
            "[BOOT_CONTINUE] orphan-sweep OFF:": True,         # sweep, OFF early return
            "[BOOT_CONTINUE] orphan-sweep instance=": True,    # sweep, success
            "[BOOT_CONTINUE] orphan-sweep summary:": True,     # sweep, summary
        }
        blocks = _logger_boot_continue_blocks(source)
        seen: dict[str, bool] = {}
        for block in blocks:
            for marker in expected:
                if marker in block:
                    # the FIRST call carrying the marker is the site
                    seen.setdefault(marker, "mode=" in block)
        assert seen == expected, (
            f"the mode= carriers drifted: {seen}"
        )


def _logger_boot_continue_blocks(source: str) -> list[str]:
    """The source text of every ``[BOOT_CONTINUE]`` logger CALL.

    Parsed from the call's opening parenthesis to its balancing close —
    docstring quotes are never included (they are not ``logger.`` calls).
    """
    blocks: list[str] = []
    for match in re.finditer(r"logger\.\w+\(", source):
        start = match.start()
        depth = 0
        for index in range(match.end() - 1, len(source)):
            char = source[index]
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    block = source[start:index + 1]
                    if "[BOOT_CONTINUE]" in block:
                        blocks.append(block)
                    break
    return blocks


# ── F-04: the intent record cannot carry the mode ─────────────────────────


def test_the_intent_write_precedes_the_pass_in_the_lifespan() -> None:
    """F-04: the intent record is written BEFORE the pass runs, so it
    can only ever carry ``unknown`` — the pass result is the sole mode
    authority."""
    api_source = (REPO_ROOT / "daemon" / "api.py").read_text(encoding="utf-8")
    intent = api_source.index("write_intent_record(")
    pass_call = api_source.index("continue_result = await continue_running_instances_after_restart")
    record_write = api_source.index("write_boot_record(")
    assert intent < pass_call < record_write

    intent_block = api_source[
        api_source.rfind("\n", 0, intent) - 400 : intent + 200
    ]
    assert "unknown" in intent_block


def test_the_boot_record_import_is_lazy_inside_the_pass_envelope() -> None:
    """RK-20: a MODULE-SCOPE import of the diagnostics package would let
    a single ImportError silently disable BOTH auto-continue passes (the
    envelope's ``except`` only WARNs). The import must be inside the
    boot-pass ``try`` block."""
    api_source = (REPO_ROOT / "daemon" / "api.py").read_text(encoding="utf-8")
    assert "from daemon.services.wedge_capture import boot_record" in api_source

    tree = ast.parse(api_source)
    module_scope_packages = [
        node.module
        for node in tree.body  # MODULE SCOPE ONLY
        if isinstance(node, ast.ImportFrom) and node.module
    ]
    assert "daemon.services.wedge_capture" not in module_scope_packages, (
        "the wedge_capture package must be imported LAZILY (inside the "
        "boot-pass envelope), never at module scope"
    )
    assert not any(
        module.startswith("daemon.services.wedge_capture")
        for module in module_scope_packages
    )

    # ... and the lazy import IS inside the boot-pass function body.
    lifespan = next(
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "lifespan"
    )
    nested = [
        node.module
        for node in ast.walk(lifespan)
        if isinstance(node, ast.ImportFrom) and node.module
    ]
    assert "daemon.services.wedge_capture" in nested


def test_the_package_init_is_empty() -> None:
    """A8 / RK-20: the package ``__init__`` imports NOTHING."""
    tree = ast.parse(
        (REPO_ROOT / "daemon" / "services" / "wedge_capture" / "__init__.py")
        .read_text(encoding="utf-8")
    )
    # A docstring and NOTHING else: any import here could break the
    # auto-continue envelope's import (A8 / RK-20).
    assert len(tree.body) == 1
    assert isinstance(tree.body[0], ast.Expr)
    assert isinstance(tree.body[0].value, ast.Constant)
    assert isinstance(tree.body[0].value.value, str)
