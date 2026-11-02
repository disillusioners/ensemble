"""The label-aware series selector (AM-1).

A series is ``(name, labels)`` and the labels are the COMPLETE
discriminator.  The door therefore exposes two selectors:

* ``?names=`` — NAME-level: it returns every label variant of the
  named metrics;
* ``?series=`` — LABEL-AWARE: ``<name>{<k>=<v>,…}`` names exactly one
  label combination.

The label part is parsed with the registry's canonical label syntax
(``k=v`` pairs separated by commas) and is compared against the
registry's canonical ``label_key`` serialization, so the selector can
never disagree with the ring keys.

An invalid selector is an EXPECTED condition: it raises
:class:`InvalidLabelFilter`, which the route maps to ``400
invalid_label_filter`` — never a 500.
"""

from __future__ import annotations

from typing import Mapping, Sequence

from daemon.checkpoint_metrics import label_key


class InvalidLabelFilter(ValueError):
    """An unparsable or unusable label selector (→ 400)."""


def parse_series_selector(text: str) -> tuple[str, dict[str, str]]:
    """Parse ``<name>`` or ``<name>{<k>=<v>,…}`` into ``(name, labels)``.

    Raises :class:`InvalidLabelFilter` for a malformed selector.
    """
    if not isinstance(text, str) or not text.strip():
        raise InvalidLabelFilter("empty series selector")
    raw = text.strip()
    if "{" not in raw:
        if "}" in raw:
            raise InvalidLabelFilter(f"unbalanced selector braces: {text!r}")
        return raw, {}
    name, _, remainder = raw.partition("{")
    name = name.strip()
    if not name:
        raise InvalidLabelFilter(f"selector has no metric name: {text!r}")
    if not remainder.endswith("}"):
        raise InvalidLabelFilter(f"unbalanced selector braces: {text!r}")
    body = remainder[:-1].strip()
    labels: dict[str, str] = {}
    if body:
        for pair in body.split(","):
            key, sep, value = pair.partition("=")
            key = key.strip()
            if not sep or not key:
                raise InvalidLabelFilter(
                    f"label pair {pair!r} is not 'key=value'"
                )
            labels[key] = value.strip()
    return name, labels


def format_series_selector(name: str, labels: Mapping[str, str] | Sequence[tuple[str, str]]) -> str:
    """Render a selector for a series (the inverse of the parser)."""
    pairs = (
        list(labels.items())
        if isinstance(labels, Mapping)
        else [(str(k), str(v)) for k, v in labels]
    )
    if not pairs:
        return name
    rendered = ",".join(f"{key}={value}" for key, value in sorted(pairs))
    return f"{name}{{{rendered}}}"


def selector_matches(
    metric_name: str, labels: Mapping[str, str], selector: tuple[str, dict[str, str]]
) -> bool:
    """Whether a series matches a parsed selector.

    A NAME-only selector matches every label variant; a LABEL-AWARE
    selector matches only the exact label combination.
    """
    selector_name, selector_labels = selector
    if selector_name != metric_name:
        return False
    if not selector_labels:
        return True
    return label_key(labels) == label_key(selector_labels)


__all__ = [
    "InvalidLabelFilter",
    "format_series_selector",
    "parse_series_selector",
    "selector_matches",
]
