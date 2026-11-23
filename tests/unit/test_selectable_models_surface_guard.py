"""Surface guard for the shipped selectable-model set (Emperor ruling 2026-11-23).

The shipped selectable surface is ``("agentic", "coding", "vision", "img-gen",
"decision")``. ``coding2`` was REMOVED from the shipped default — and only
from the shipped default. The token is still legal as an EXPLICIT operator
value (via ``OPENAI_SELECTABLE_MODELS`` or the legacy ``OPENAI_ALLOWED_MODELS``
alias), which is why the legacy-alias path below is asserted to be honored
verbatim.

This module is the FUNCTIONAL pin for the ruling. It complements:

* ``tests/unit/test_llm_allowed_models_precedence.py::TestDefaultConsistencyPin``
  — the drift detector between ``daemon/config.py::_ALLOWED_MODELS_DEFAULT``
  and the ``config.yaml`` interpolation literal (kept intact; NOT weakened).
* ``daemon/config.py::_ALLOWED_MODELS_DEFAULT`` — the production single
  source of truth (owned by another module; this test only IMPORTS it).

Pinned here:

(a) the production constant equals the five-element shipped default and does
    NOT contain ``coding2``;
(b) the ``config.yaml`` ``${OPENAI_SELECTABLE_MODELS:-...}`` fallback equals
    the comma-joined constant and does NOT contain ``coding2``;
(c) the frontend carries NO hardcoded selectable-model list — zero
    ``coding2`` occurrences under ``frontend/src`` (``*.spec.ts`` and
    ``node_modules`` excluded) and no array literal that mixes a ``coding``
    literal with an ``agentic`` literal (the daemon-resolved set is the only
    source of truth);
(d) an explicit ``OPENAI_SELECTABLE_MODELS`` value is returned VERBATIM — the
    resolver does NOT join the remaining default members;
(e) with NEITHER env var set the resolved default contains no ``coding2``;
(f) the legacy alias path: ``OPENAI_ALLOWED_MODELS=agentic,coding,coding2``
    resolves VERBATIM — the ONE path where a deployment can still name
    ``coding2`` explicitly.

No network, no DB, no fixtures beyond ``monkeypatch``.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_YAML = REPO_ROOT / "config.yaml"
FRONTEND_SRC = REPO_ROOT / "frontend" / "src"

# The ONE copy of the shipped default inside this module. Every other
# assertion imports the production constant from ``daemon.config`` so the
# test can never drift into a second, independent truth.
_EXPECTED_SHIPPED_DEFAULT: tuple[str, ...] = (
    "agentic",
    "coding",
    "vision",
    "img-gen",
    "decision",
)

# The token retired from the shipped default (still legal as an explicit
# operator value — see the legacy-alias leg below).
_RETIRED_MODEL_TOKEN = "coding2"

# Same interpolation shape the existing drift pin
# (``test_llm_allowed_models_precedence.py::TestDefaultConsistencyPin``)
# matches against the repo-root ``config.yaml``.
_YAML_FALLBACK_RE = re.compile(
    r"^\s*allowed_models:\s*\$\{OPENAI_SELECTABLE_MODELS:-([^}]+)\}\s*$",
    re.MULTILINE,
)

# Frontend scan helpers -------------------------------------------------
# An "array literal" = a bracketed run with no nested brackets (innermost
# first, so ``[['coding','agentic']]`` is still caught via its inner pair).
_ARRAY_LITERAL_RE = re.compile(r"\[[^\[\]]*\]", re.DOTALL)
_QUOTE = r"(['\"`])"
_CODING_LITERAL_RE = re.compile(_QUOTE + r"coding" + r"\1")
_AGENTIC_LITERAL_RE = re.compile(_QUOTE + r"agentic" + r"\1")


def _load_config():
    """``load_config`` against the repo-root ``config.yaml``.

    The path is passed EXPLICITLY so the resolved value does not depend on
    the process CWD (``load_config()`` defaults to ``./config.yaml``).
    """
    from daemon.config import load_config

    return load_config(config_path=str(CONFIG_YAML))


def _iter_frontend_sources() -> list[Path]:
    """Every non-spec file under ``frontend/src`` (``node_modules`` excluded)."""
    assert FRONTEND_SRC.is_dir(), (
        f"frontend source root not found at {FRONTEND_SRC} — the "
        "no-hardcoded-selectable-model-list guard needs the real tree to scan"
    )
    paths: list[Path] = []
    for path in sorted(FRONTEND_SRC.rglob("*")):
        if not path.is_file():
            continue
        if "node_modules" in path.parts:
            continue
        if path.name.endswith(".spec.ts"):
            continue
        paths.append(path)
    return paths


# ---------------------------------------------------------------------------
# (a) the production constant
# ---------------------------------------------------------------------------


def test_shipped_default_constant_is_the_five_element_tuple() -> None:
    """(a) ``daemon.config._ALLOWED_MODELS_DEFAULT`` is the shipped
    five-element default and does NOT contain ``coding2``.
    """
    from daemon.config import _ALLOWED_MODELS_DEFAULT

    assert _ALLOWED_MODELS_DEFAULT == _EXPECTED_SHIPPED_DEFAULT, (
        "selectable-model default drifted: "
        f"{_ALLOWED_MODELS_DEFAULT} != {_EXPECTED_SHIPPED_DEFAULT}. The "
        "shipped surface is agentic,coding,vision,img-gen,decision — "
        "'coding2' was removed by the 2026-11-23 ruling."
    )
    assert _RETIRED_MODEL_TOKEN not in _ALLOWED_MODELS_DEFAULT


# ---------------------------------------------------------------------------
# (b) the config.yaml interpolation literal
# ---------------------------------------------------------------------------


def test_config_yaml_fallback_matches_constant_and_drops_coding2() -> None:
    """(b) The ``config.yaml`` ``${OPENAI_SELECTABLE_MODELS:-...}`` fallback
    equals the comma-joined constant and does NOT contain ``coding2``.
    """
    from daemon.config import _ALLOWED_MODELS_DEFAULT

    assert CONFIG_YAML.is_file(), (
        f"config.yaml not found at {CONFIG_YAML} — the surface guard needs "
        "the repo-root YAML to extract the interpolation fallback"
    )
    text = CONFIG_YAML.read_text(encoding="utf-8")
    match = _YAML_FALLBACK_RE.search(text)
    assert match is not None, (
        "Could not find the allowed_models interpolation line in config.yaml "
        "— expected the ${OPENAI_SELECTABLE_MODELS:-...} form"
    )
    fallback = match.group(1).strip()
    assert fallback == ",".join(_ALLOWED_MODELS_DEFAULT), (
        f"DRIFT: config.yaml fallback {fallback!r} != comma-joined "
        f"_ALLOWED_MODELS_DEFAULT {','.join(_ALLOWED_MODELS_DEFAULT)!r}"
    )
    fallback_members = [m.strip() for m in fallback.split(",") if m.strip()]
    assert _RETIRED_MODEL_TOKEN not in fallback_members, (
        f"config.yaml fallback still names {_RETIRED_MODEL_TOKEN!r}: "
        f"{fallback_members}"
    )


# ---------------------------------------------------------------------------
# (c) the frontend carries no hardcoded selectable-model list
# ---------------------------------------------------------------------------


def test_frontend_contains_zero_coding2_occurrences() -> None:
    """(c1) Zero ``coding2`` occurrences anywhere under ``frontend/src``
    (``*.spec.ts`` and ``node_modules`` excluded).

    Scan implementation: every non-spec file under ``frontend/src`` is read
    as raw BYTES and searched for the literal token ``b"coding2"`` (byte-level
    so no encoding/decoding gap can hide a hit).
    """
    offenders: list[str] = []
    scanned = 0
    for path in _iter_frontend_sources():
        scanned += 1
        if _RETIRED_MODEL_TOKEN.encode("utf-8") in path.read_bytes():
            offenders.append(str(path.relative_to(REPO_ROOT)))
    assert scanned > 0, (
        "frontend scan examined 0 files — the guard would be vacuous"
    )
    assert offenders == [], (
        "frontend must not hardcode the retired selectable-model token "
        f"{_RETIRED_MODEL_TOKEN!r}; found in: {offenders}"
    )


def test_frontend_has_no_array_literal_mixing_coding_and_agentic() -> None:
    """(c2) No frontend file contains ONE array literal that holds BOTH a
    ``coding`` model literal AND an ``agentic`` model literal.

    Scan implementation: every non-spec ``.ts`` file under ``frontend/src``
    is scanned for innermost bracketed literals (``\\[[^\\[\\]]*\\]``); a
    literal is an offender when it contains a quoted ``coding`` element AND a
    quoted ``agentic`` element. The daemon-resolved set is the single source
    of truth — the frontend must not carry its own selectable-model list.
    """
    offenders: list[str] = []
    scanned = 0
    for path in _iter_frontend_sources():
        if path.suffix != ".ts":
            continue
        scanned += 1
        text = path.read_text(encoding="utf-8", errors="ignore")
        for match in _ARRAY_LITERAL_RE.finditer(text):
            literal = match.group(0)
            if (
                _CODING_LITERAL_RE.search(literal)
                and _AGENTIC_LITERAL_RE.search(literal)
            ):
                offenders.append(
                    f"{path.relative_to(REPO_ROOT)}: {literal[:120]!r}"
                )
    assert scanned > 0, (
        "frontend .ts scan examined 0 files — the guard would be vacuous"
    )
    assert offenders == [], (
        "frontend must not hardcode a selectable-model array literal "
        f"(coding + agentic in one literal): {offenders}"
    )


# ---------------------------------------------------------------------------
# (d) explicit value resolves verbatim
# ---------------------------------------------------------------------------


def test_explicit_selectable_models_resolve_verbatim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """(d) ``OPENAI_SELECTABLE_MODELS=agentic,coding,vision`` resolves to
    exactly that list — the resolver does NOT join ``img-gen``/``decision``
    and does NOT re-add ``coding2``.
    """
    monkeypatch.setenv("OPENAI_SELECTABLE_MODELS", "agentic,coding,vision")
    monkeypatch.delenv("OPENAI_ALLOWED_MODELS", raising=False)

    cfg = _load_config()

    assert cfg.llm.allowed_models == ["agentic", "coding", "vision"]
    assert _RETIRED_MODEL_TOKEN not in cfg.llm.allowed_models
    # The legacy alias must be UNSET in this leg — the new name is the
    # effective source.
    assert os.environ.get("OPENAI_ALLOWED_MODELS") is None


# ---------------------------------------------------------------------------
# (e) neither env var set → the shipped default, without coding2
# ---------------------------------------------------------------------------


def test_resolved_default_has_no_coding2_when_no_env_var_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """(e) With NEITHER env var set the resolved default is the shipped
    five-element default and contains no ``coding2``.
    """
    monkeypatch.delenv("OPENAI_SELECTABLE_MODELS", raising=False)
    monkeypatch.delenv("OPENAI_ALLOWED_MODELS", raising=False)

    cfg = _load_config()

    assert cfg.llm.allowed_models == list(_EXPECTED_SHIPPED_DEFAULT)
    assert _RETIRED_MODEL_TOKEN not in cfg.llm.allowed_models


# ---------------------------------------------------------------------------
# (f) the legacy alias path — the one path that may still name coding2
# ---------------------------------------------------------------------------


def test_legacy_alias_is_honored_verbatim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """(f) ``OPENAI_ALLOWED_MODELS=agentic,coding,coding2`` (legacy alias
    only) resolves VERBATIM.

    This is the ONE path where a deployment can still name ``coding2``
    explicitly: the alias is honored as-is and is NOT re-resolved against
    the shipped default.
    """
    monkeypatch.delenv("OPENAI_SELECTABLE_MODELS", raising=False)
    monkeypatch.setenv("OPENAI_ALLOWED_MODELS", "agentic,coding,coding2")

    cfg = _load_config()

    assert cfg.llm.allowed_models == ["agentic", "coding", "coding2"]
