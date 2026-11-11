"""The prompt-assent Phase-4 STRUCTURAL pins (task 4.6 + deliverable 7).

SP-1 (rewritten to the rendered canonical bytes — K-7) and SP-2 (no
UPDATE/DELETE against ``prompt_audit_events``) landed with their
demonstrated counter-mutations in the Phase-1/Phase-3 files
(``tests/unit/test_prompt_assent_column_source.py`` /
``tests/unit/test_prompt_assent_audit_payload.py`` /
``tests/unit/test_prompt_assent_counter_mutations.py``); the lane pack
collects and runs them. THIS file carries the two pins PHASE 4 owns:

* **the shared-constant pin** — the D-2 default-OFF constant exists ONCE
  (``daemon/config.py``, beside the ``PromptAssentConfig`` class) and
  BOTH consumers — that class and the tool's call-time resolver — read
  the SAME object. Not two declared defaults (architect §7 defect 6).
* **the retired-spelling pin** — the planner-drift spellings
  ``PromptAssistConfig`` / ``ENSEMBLE_PROMPT_ASSIST_`` (architect §7
  defect 1, H-11) survive NOWHERE in the tree, EXCEPT the one file that
  quotes them on purpose (``phase4-plan.md`` — they are the literal
  strings THIS pin greps for, per the plan's own header).

NON-VACUITY (the house mutation rule — absence-only and raw-text pins
are prose-satisfiable): each pin ships with an IN-FILE counter-mutation
demonstration that makes its own check RAISE, and the lane report
records the LIVE tree-mutation runs (flip the shared constant; plant a
retired spelling) with byte-identical restores.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import daemon.config as config_module
import daemon.tools.prompt_assent as tool_module
from daemon.config import (
    PROMPT_ASSENT_DEFAULT_ENABLED,
    PROMPT_ASSENT_DEFAULT_HOURLY_MISMATCH_BUDGET,
    PromptAssentConfig,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

#: The two RETIRED spellings (architect §7 defect 1) — the literal grep
#: needles. ``phase4-plan.md`` quotes them ON PURPOSE (they are what
#: this pin greps for); it is the ONLY allowed occurrence in the tree.
RETIRED_SPELLINGS: tuple[str, ...] = (
    "PromptAssistConfig",
    "ENSEMBLE_PROMPT_ASSIST_",
)

#: The files ALLOWED to carry the retired spellings: the plan's own
#: header (they are quoted on purpose — they are what this pin greps
#: for) and THIS pin file itself (the needle definitions + the
#: synthetic counter-mutation probe must hold the literals to detect
#: them).
RETIRED_SPELLING_EXEMPT: frozenset[str] = frozenset({
    ".agents/shared/planning/prompt-assent/phase4-plan.md",
    "tests/unit/test_prompt_assent_pins.py",
})

#: Directories that never carry lane sources — not scanned (venv
#: wheels and git internals are not the tree).
_SKIPPED_TOP = frozenset({".git", ".venv", "node_modules", "__pycache__"})


def _tracked_files() -> list[Path]:
    """The git-tracked files of THIS tree (deterministic; no WIP noise)."""
    out = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files", "-z"],
        capture_output=True,
        check=True,
    ).stdout
    files: list[Path] = []
    for raw in out.split(b"\0"):
        if not raw:
            continue
        rel = raw.decode("utf-8", "surrogateescape")
        if rel.split("/", 1)[0] in _SKIPPED_TOP and "/" in rel:
            continue
        path = REPO_ROOT / rel
        if path.is_file():
            files.append(path)
    return files


def _retired_spelling_hits(files: list[Path]) -> list[str]:
    """Every tracked file (minus the exempt doc) carrying a RETIRED
    spelling — returned as ``<path>: <needle>`` violations."""
    hits: list[str] = []
    for path in files:
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel in RETIRED_SPELLING_EXEMPT:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue  # binary — not a lane source
        for needle in RETIRED_SPELLINGS:
            if needle in text:
                hits.append(f"{rel}: {needle}")
    return hits


def _assert_both_consumers() -> None:
    """The shared-constant pin's own assertion (the mutation target)."""
    # The tool module IMPORTS the constant — the same OBJECT the
    # config module declares. A second literal (a re-declaration) would
    # break the identity.
    assert (
        tool_module.PROMPT_ASSENT_DEFAULT_ENABLED
        is config_module.PROMPT_ASSENT_DEFAULT_ENABLED
    ), "the tool resolver does not read the config module's constant"
    # D-2: the shared default is OFF — fail-closed.
    assert config_module.PROMPT_ASSENT_DEFAULT_ENABLED is False, (
        "D-2: the ONE shared default must be OFF (fail-closed)"
    )
    # The class's field default IS that same object (bound at class
    # definition from the module constant).
    assert (
        PromptAssentConfig.model_fields["enabled"].default
        is config_module.PROMPT_ASSENT_DEFAULT_ENABLED
    ), "the class field default is not the shared constant"
    # The resolver constructs the SAME class (one knob authority).
    assert tool_module.PromptAssentConfig is config_module.PromptAssentConfig
    # End-to-end: with no env and no config, the resolver lands on the
    # shared OFF default.
    assert tool_module._resolve_enabled(None) is False


def _assert_budget_default_shared() -> None:
    """The K-9 default's own assertion (the mutation target)."""
    assert (
        tool_module.PROMPT_ASSENT_DEFAULT_HOURLY_MISMATCH_BUDGET
        is config_module.PROMPT_ASSENT_DEFAULT_HOURLY_MISMATCH_BUDGET
    ), "the budget default is re-declared, not imported"
    assert config_module.PROMPT_ASSENT_DEFAULT_HOURLY_MISMATCH_BUDGET == 10
    assert (
        PromptAssentConfig.model_fields["hourly_mismatch_budget"].default
        is config_module.PROMPT_ASSENT_DEFAULT_HOURLY_MISMATCH_BUDGET
    )
    assert tool_module._resolve_hourly_budget(None) == 10


def _assert_no_redeclaration(source_text: str) -> None:
    """The single-home source check (the registration file's pin shape)
    factored out so the counter-mutation can target it directly."""
    assert (
        "PROMPT_ASSENT_DEFAULT_ENABLED: bool" not in source_text
    ), "the tool module re-declares the shared default"
    assert (
        "PROMPT_ASSENT_DEFAULT_HOURLY_MISMATCH_BUDGET: int"
        not in source_text
    ), "the tool module re-declares the shared budget default"


# ── the retired-spelling pin (H-11 / architect §7 defect 1) ─────────────


class TestTheRetiredSpellingPin:
    def test_no_retired_spelling_survives_anywhere(self) -> None:
        """A grep proves NO retired spelling survives anywhere in the
        tree — except the one doc that quotes them as the needles."""
        hits = _retired_spelling_hits(_tracked_files())
        assert hits == [], (
            "retired prompt-assist spellings survived: "
            f"{hits}\n(the ONLY allowed occurrence is the exempt "
            "phase4-plan.md quoting)"
        )

    def test_the_exempt_doc_is_the_only_allowed_occurrence(self) -> None:
        """The exempt doc really DOES carry the needles (the scan is not
        vacuously passing on an empty needle set)."""
        doc = REPO_ROOT / ".agents/shared/planning/prompt-assent/phase4-plan.md"
        assert doc.is_file(), "the exempt doc vanished — re-point the pin"
        text = doc.read_text(encoding="utf-8")
        for needle in RETIRED_SPELLINGS:
            assert needle in text, f"{needle} is no longer quoted in the doc"

    def test_the_scanner_catches_the_shape(self) -> None:
        """NON-VACUITY (in-file counter-mutation): the scanner REDS on a
        synthetic source carrying the retired spelling — the same
        reddening the LIVE tree mutation demonstrates in the lane
        report."""
        synthetics = [
            "class PromptAssistConfig(BaseSettings):\n    enabled: bool = False\n",
            "KILL_SWITCH_ENV = 'ENSEMBLE_PROMPT_ASSIST_ENABLED'\n",
        ]
        for source in synthetics:
            probe = REPO_ROOT / "tests" / "unit" / "_pin_mutation_probe.py"
            probe.write_text(source, encoding="utf-8")
            try:
                hits = _retired_spelling_hits([probe])
            finally:
                probe.unlink(missing_ok=True)
            assert hits, (
                "the scanner missed the retired spelling — the pin is "
                "vacuous"
            )

    def test_the_probe_never_stays_behind(self) -> None:
        """The mutation probe above must leave NO residue (the
        restore-discipline pin)."""
        probe = REPO_ROOT / "tests" / "unit" / "_pin_mutation_probe.py"
        assert not probe.exists()


# ── the shared-constant pin (D-2 / architect §7 defect 6) ───────────────


class TestTheSharedConstantPin:
    def test_both_consumers_read_the_same_constant(self) -> None:
        """ONE constant, TWO consumers: the ``PromptAssentConfig`` class
        and the tool's call-time resolver."""
        _assert_both_consumers()

    def test_the_budget_default_is_equally_single(self) -> None:
        """K-9/D-3b — the budget default rides the same discipline."""
        _assert_budget_default_shared()

    def test_the_constant_flip_mutation_reddens_the_pin(self, monkeypatch):
        """NON-VACUITY (in-file counter-mutation): flipping the shared
        constant to ``True`` (the live tree mutation demonstrated in the
        lane report) makes the pin RAISE."""
        monkeypatch.setattr(
            config_module, "PROMPT_ASSENT_DEFAULT_ENABLED", True, raising=False
        )
        with pytest.raises(AssertionError):
            _assert_both_consumers()

    def test_the_budget_default_drift_mutation_reddens_the_pin(
        self, monkeypatch
    ):
        """NON-VACUITY (in-file counter-mutation): drifting the budget
        default makes its pin RAISE."""
        monkeypatch.setattr(
            config_module,
            "PROMPT_ASSENT_DEFAULT_HOURLY_MISMATCH_BUDGET",
            25,
            raising=False,
        )
        with pytest.raises(AssertionError):
            _assert_budget_default_shared()

    def test_the_redeclared_literal_mutation_reddens_the_pin(self):
        """NON-VACUITY (in-file counter-mutation): re-declaring the
        constant in the TOOL module (a second literal — the exact defect
        the architect closed, §7 defect 6) makes the single-home source
        check RAISE."""
        tool_source = (
            REPO_ROOT / "daemon" / "tools" / "prompt_assent.py"
        ).read_text(encoding="utf-8")
        _assert_no_redeclaration(tool_source)  # the real tree: silent
        mutated = tool_source + (
            "\nPROMPT_ASSENT_DEFAULT_ENABLED: bool = True\n"
        )
        with pytest.raises(AssertionError):
            _assert_no_redeclaration(mutated)

    def test_the_resolver_default_survives_an_absent_config(self) -> None:
        """No env, no config ⇒ OFF — the shared constant's value, read
        through the real resolver."""
        import os

        saved = os.environ.pop("ENSEMBLE_PROMPT_ASSENT_ENABLED", None)
        try:
            assert tool_module._resolve_enabled(None) is False
            assert tool_module._resolve_enabled(object()) is False
        finally:
            if saved is not None:
                os.environ["ENSEMBLE_PROMPT_ASSENT_ENABLED"] = saved
