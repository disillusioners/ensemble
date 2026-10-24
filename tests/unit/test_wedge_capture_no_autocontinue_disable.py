"""Kill-switch-absence pack (T35 / AC49 / SC-10 / BC-1).

THE RULE THIS PACK ENFORCES
---------------------------
**Auto-resume stays ON, default ON.** The patch may never carry a
persisted disable: not in ``.env``, not in ``.env.example``, not in a
systemd unit, not in source, not in a test fixture that makes the
instrument's behaviour deterministic by turning the feature off.

WHAT IS *NOT* A VIOLATION
-------------------------
* **Prose.** The prohibition itself is documented with the token, and
  the planning artifacts quote it. Documentation is exempt.
* **A monkeypatched, auto-restored OFF-branch test.** The boot-record
  pack MUST drive the OFF early-return branch (SC-8 / AC45) — it does so
  through ``monkeypatch``, which is restored at teardown, and it never
  writes the value to a file. That single file is pinned below.

THE MUTATION LEG
----------------
``_added_lines`` is the pure comparison the live assertions use. It is
exercised red/green on synthetic inputs, so the gate is provably able to
FAIL — not merely observed passing.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BASE_REVISION = "639b7129b"

#: The kill-switch NAME — assembled from parts so this file never carries
#: the literal disable token it hunts for.
KILL_SWITCH = "ENSEMBLE_AUTO_CONTINUE" + "_RUNNING_ON_RESTART"
DISABLE_VALUE = '"' + "0" + '"'
DISABLE_TOKEN = KILL_SWITCH + "=" + "0"

#: The CODE/CONFIG surface the gate covers — prose directories
#: (``.agents/``, ``docs/``) are exempt by design.
CODE_PATH_PREFIXES = (
    "daemon/",
    "tests/",
    "scripts/",
    "agents/",
    "launcher.sh",
    "config.yaml",
    "dev.sh",
    "run_app.py",
)
CODE_PATH_SUFFIXES = (".service", ".env", ".example", ".sh", ".py", ".yaml", ".toml")

#: The ONE pack allowed to drive the OFF branch (monkeypatched only).
OFF_BRANCH_PACK = "tests/unit/test_wedge_capture_boot_record.py"


def _is_code_path(path: str) -> bool:
    """Is this path part of the executable/config surface?"""
    if path.startswith(".agents/") or path.startswith("docs/"):
        return False
    if path.startswith(".env"):
        return True
    if path.endswith(CODE_PATH_SUFFIXES):
        return True
    return path.startswith(CODE_PATH_PREFIXES)


def _added_lines(base_text: str, head_text: str) -> list[str]:
    """Lines present in ``head_text`` but NOT in ``base_text``.

    The live gate's comparison, extracted so the mutation leg can prove
    it FAILS when it should.
    """
    base = set(base_text.splitlines())
    return [line for line in head_text.splitlines() if line not in base]


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=False
    )


def _tracked_files() -> list[str]:
    completed = _git("ls-files")
    if completed.returncode != 0:
        pytest.skip(f"git unavailable: {completed.stderr[:200]}")
    return [line for line in completed.stdout.splitlines() if line]


def _read_at_revision(revision: str, path: str) -> str | None:
    completed = _git("show", f"{revision}:{path}")
    return completed.stdout if completed.returncode == 0 else None


# ── the pure comparison (the mutation leg) ────────────────────────────────


class TestTheComparisonItself:
    def test_it_sees_an_added_occurrence(self) -> None:
        base = "a\nb\n"
        head = "a\nb\n" + "# arm the lever\nexport " + DISABLE_TOKEN + "\n"
        added = _added_lines(base, head)
        assert any(DISABLE_TOKEN in line for line in added)

    def test_it_sees_nothing_when_only_prose_moved(self) -> None:
        base = "a\nb\n"
        head = "a\nb\nc\n"
        assert _added_lines(base, head) == ["c"]

    def test_the_needle_is_not_accidentally_the_var_name_alone(self) -> None:
        """The token requires the ``=0`` form — mentioning the NAME is
        fine (the AUTO_CONTINUE machinery does exactly that)."""
        assert DISABLE_TOKEN not in KILL_SWITCH
        assert DISABLE_TOKEN.startswith(KILL_SWITCH)
        assert DISABLE_TOKEN.endswith("=0")


# ── the live gate: no ADDED occurrence on the code/config surface ────────


class TestNoAddedDisable:
    def test_no_code_or_config_file_adds_the_disable_token(self) -> None:
        offenders: list[str] = []
        for path in _tracked_files():
            if not _is_code_path(path):
                continue
            try:
                head_text = (REPO_ROOT / path).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if DISABLE_TOKEN not in head_text:
                continue
            base_text = _read_at_revision(BASE_REVISION, path)
            if base_text is None:
                # A NEW file that carries the token is a violation.
                offenders.append(path)
                continue
            if any(DISABLE_TOKEN in line for line in _added_lines(base_text, head_text)):
                offenders.append(path)
        assert offenders == [], (
            "the patch ADDS a persisted auto-continue disable in: "
            f"{offenders} (BC-1: auto-resume stays ON, default ON)"
        )

    def test_the_env_example_carries_no_disable(self) -> None:
        text = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
        assert DISABLE_TOKEN not in text
        assert re.search(
            rf"{KILL_SWITCH}\s*=\s*[\"']?0", text
        ) is None

    def test_no_systemd_unit_carries_a_disable(self) -> None:
        units = list((REPO_ROOT / "scripts" / "systemd").glob("*.service"))
        assert units, "the systemd unit set is expected to exist"
        for unit in units:
            text = unit.read_text(encoding="utf-8")
            assert DISABLE_TOKEN not in text, unit
            assert f"{KILL_SWITCH}=" not in text, (
                f"{unit} must not SET the kill-switch at all — the "
                "emergency lever is an operator action, not a unit default"
            )

    def test_the_wedge_env_block_documents_exactly_the_fifteen_knobs(self) -> None:
        """AC26's cheap companion: the new block exists and the count is
        15 — a 14- or 16-knob block is drift."""
        from daemon.services.wedge_capture.config import ENV_KNOB_NAMES

        assert len(ENV_KNOB_NAMES) == 15
        text = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
        for knob in ENV_KNOB_NAMES:
            assert knob in text, f"{knob} is undocumented in .env.example"

    def test_no_other_kill_switch_enable_disable_leaks_into_the_diff(self) -> None:
        """The instrument's OWN lever is fine; the AUTO-CONTINUE lever may
        only be READ by the boot record (never set)."""
        boot_record = (
            REPO_ROOT / "daemon" / "services" / "wedge_capture" / "boot_record.py"
        ).read_text(encoding="utf-8")
        # the module transcribes the NAME (for the verbatim transcript)
        assert KILL_SWITCH in boot_record
        # ... but never assigns it
        assert not re.search(
            rf"{KILL_SWITCH}\s*[\"']?\s*[=:]\s*[\"']?[01]", boot_record
        )
        assert "os.environ[" + '"' + KILL_SWITCH + '"' + "]" not in boot_record


# ── the one sanctioned OFF-branch consumer ───────────────────────────────


class TestTheOffBranchPackIsTheOnlyConsumer:
    def test_only_the_boot_record_pack_touches_the_switch(self) -> None:
        """The gate itself is excluded (it must spell the NAME to hunt
        for the token); every OTHER wedge pack must stay clear."""
        gate = str(Path(__file__).resolve().relative_to(REPO_ROOT))
        wedge_packs = sorted((REPO_ROOT / "tests").rglob("test_wedge_capture_*.py"))
        assert wedge_packs, "the wedge packs must exist"
        consumers = [
            str(path.relative_to(REPO_ROOT))
            for path in wedge_packs
            if str(path.relative_to(REPO_ROOT)) != gate
            and (
                "ENSEMBLE_AUTO_CONTINUE" in path.read_text(encoding="utf-8")
                or "AUTO_CONTINUE_KILL_SWITCH_ENV" in path.read_text(encoding="utf-8")
            )
        ]
        assert consumers == [OFF_BRANCH_PACK], (
            "only the boot-record pack may drive the OFF branch: "
            f"{consumers}"
        )

    def test_the_consumer_sets_it_through_monkeypatch_only(self) -> None:
        text = (REPO_ROOT / OFF_BRANCH_PACK).read_text(encoding="utf-8")
        for index, line in enumerate(text.splitlines(), start=1):
            if DISABLE_TOKEN in line:
                pytest.fail(
                    f"{OFF_BRANCH_PACK}:{index} spells the literal disable "
                    "token — drive the branch through monkeypatch.setenv("
                    "AUTO_CONTINUE_KILL_SWITCH_ENV, ...) instead"
                )
            if "AUTO_CONTINUE_KILL_SWITCH_ENV" in line and "setenv" in line:
                assert "monkeypatch.setenv" in line, (
                    f"{OFF_BRANCH_PACK}:{index} must use monkeypatch.setenv "
                    "(auto-restored), never a raw os.environ write"
                )
