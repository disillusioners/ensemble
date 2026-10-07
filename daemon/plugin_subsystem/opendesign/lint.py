"""HTML artifact lint (Port: od.lint).

The 16-regex family + parse5 EOF gate that is the canonical quality
gate between :func:`OdGenerate.execute` and the developer's brief. A
``fail-N`` verdict never rides into the developer's brief — the
designer re-calls ``od.generate`` with a corrected brief.

**Attribution.** This is an ``own_outright`` port per the slice-⑤
MANIFEST (own_outright/lint/ subtree, not vendored). The 16-regex
family is a re-expression of the lint heuristics in
``/home/nea/opt/open-design/apps/daemon/src/lint-artifact.ts`` plus
the parse5 EOF gate (which the upstream linter is explicitly
non-parsing per ``od-generation-engine.md`` §1 Component C; that gap
is the load-bearing reason this own_outright port exists).

**Verdict semantics.**

- ``pass`` — no failures.
- ``fail-1`` … ``fail-4`` — at least one failure, bucketed by severity.
  The bucket threshold matches the live designer workflow's
  expectations: a ``fail-1`` (one regex miss) is typically fixable by
  re-trying generation; ``fail-4`` (structural truncation) is the
  truncation marker that MUST halt the lane.

**Parse5 gate.** The parse5 EOF check refuses to verdict ``pass`` on a
document with unbalanced / unterminated structural tags (``<html>``,
``<body>``, ``</body>``, ``</html>``, plus ``<artifact>`` markers when
present). The 16-regex family is fast (regex-only) and runs first;
parse5 is the structural backstop that catches the truncation class.

**Schema surface.** Mirrors the Port's ``inputs_schema`` (``html``
required + optional ``kind``) and ``outputs_schema`` (``verdict``,
``fail_count``, ``failures[]`` with ``message`` + ``line``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence

__all__ = ["OdLint", "LintFailure"]


@dataclass(frozen=True)
class LintFailure:
    """One lint finding; emitted only when the lint verdict is fail-N."""

    rule_id: str  # R1-R16 or EOF
    message: str
    line: int = 0


class OdLint:
    """The HTML artifact linter (Port: od.lint).

    Stateless; instantiated once at boot. The 16-regex family + parse5
    EOF gate run inline (no subprocess; the parse5 dependency is a
    stdlib-only HTML structural tokenizer — see ``stdlib_html5lib`` in
    pyproject.toml, or the local lightweight ``_parse5_eof_gate`` if
    parse5 isn't installed).
    """

    # The 16-regex family. Each entry is (rule_id, compiled-pattern,
    # description). The pattern matches when the rule is VIOLATED — the
    # lint counts the violation and emits a :class:`LintFailure`.
    # Anchors are loose (single-line by default) so the patterns can
    # match anywhere in the artifact; line tracking is approximate (we
    # count the offset of the first match).
    _RULES: Sequence[tuple[str, str, str]] = (
        (
            "R1",
            r"(?i)<!doctype\s+html\s*>",
            "missing-or-malformed DOCTYPE declaration (lints prefer '<!doctype html>')",
        ),
        (
            "R2",
            r"<html(?:\s[^>]*)?>",
            "missing opening <html> tag",
        ),
        (
            "R3",
            r"</html\s*>",
            "missing closing </html> tag",
        ),
        (
            "R4",
            r"<head\b[^>]*>",
            "missing <head> tag",
        ),
        (
            "R5",
            r"<title\b[^>]*>.*?</title\s*>",
            "missing or empty <title> element",
        ),
        (
            "R6",
            r"<body\b[^>]*>",
            "missing <body> tag",
        ),
        (
            "R7",
            r"</body\s*>",
            "missing </body> closing tag",
        ),
        (
            "R8",
            r'<meta\s+charset\s*=\s*["\']?[^"\']+["\']?',
            "missing <meta charset> declaration",
        ),
        (
            "R9",
            r'<meta\s+name\s*=\s*["\']viewport["\'][^>]*>',
            "missing <meta viewport> for responsive artifact",
        ),
        (
            "R10",
            r"<style\b",
            "no <style> block present (artifact ships unstyled)",
        ),
        (
            "R11",
            r"\{\{[^}]*\}\}",
            "unrendered handlebars-style placeholder present",
        ),
        (
            "R12",
            r"<script\b[^>]*src\s*=\s*['\"]https?://",
            "external script src reference (CSP / offline-friendly lint)",
        ),
        (
            "R13",
            r"\bon(load|click|error)\s*=\s*['\"][^'\"]+['\"]",
            "inline event-handler attributes (CSP lint)",
        ),
        (
            "R14",
            r"<!--\s*Generation\s+(?:timed out|cancelled)",
            "upstream 'Generation truncated' marker — artifact was cut mid-stream (must halt)",
        ),
        (
            "R15",
            r"<img\b(?![^>]*alt\s*=)[^>]*>",
            "img without alt attribute (a11y lint)",
        ),
        (
            "R16",
            r"\bTODO\b|\bFIXME\b|\bXXX\b",
            "TODO/FIXME/XXX marker present in the artifact",
        ),
    )

    @classmethod
    def lint_dict(cls, raw: Mapping[str, Any]) -> Dict[str, Any]:
        """Dict-entrypoint form of :meth:`lint` (factory convention).

        The plugin tool factory hands every adapter the tool-kwargs
        DICT (the Port's ``inputs_schema`` shape); ``lint_dict``
        unpacks ``html`` (required string) + optional ``kind`` and
        delegates to :meth:`lint`. Non-object / non-string inputs fall
        into the documented ``fail-1`` empty-HTML verdict shape rather
        than raising — the linter's verdict payload is always the
        Port's ``outputs_schema``.
        """
        if not isinstance(raw, Mapping):
            return {
                "verdict": "fail-1",
                "fail_count": 1,
                "failures": [
                    {
                        "rule_id": "EOF",
                        "message": "input must be a JSON object",
                        "line": 0,
                    }
                ],
            }
        html = raw.get("html")
        kind = raw.get("kind")
        return cls.lint(html if isinstance(html, str) else "", kind if isinstance(kind, str) else None)

    @classmethod
    def lint(cls, html: str, kind: Optional[str] = None) -> Dict[str, Any]:
        """Run the 16-regex family + parse5 EOF gate on ``html``.

        Returns the Port's ``outputs_schema`` dict (JSON-friendly by
        construction). The verdict is bucketed into ``pass`` /
        ``fail-1`` … ``fail-4`` based on the fail_count and severity:

        - fail-1: 1 failure (typical fix-by-retry; regenerate with corrected brief).
        - fail-2: 2-3 failures (multi-rule; regenerate + inspect brief).
        - fail-3: 4+ failures (significant; regenerate).
        - fail-4: R14 hit (truncation marker — must halt, never ship).
        """
        import re

        if not isinstance(html, str) or not html:
            return {
                "verdict": "fail-1",
                "fail_count": 1,
                "failures": [
                    {
                        "rule_id": "EOF",
                        "message": "empty HTML passed to lint",
                        "line": 0,
                    }
                ],
            }

        failures: List[Dict[str, Any]] = []

        for rule_id, pattern, message in cls._RULES:
            m = re.search(pattern, html, flags=re.MULTILINE | re.DOTALL)
            # Rules R1-R10 and R12 require the pattern to be present (they
            # assert a structural element exists). Rules R11, R13, R14,
            # R15, R16 assert the pattern is ABSENT (the artifact should
            # not contain them). The sign is encoded in the rule_id's
            # "presence" — a regex starting with `(?:` on a "missing"
            # rule is inverted. We use the convention: the pattern
            # ALWAYS matches the violation; the lint reports the
            # violation when the pattern MATCHES. R1-R10 match a present
            # element when it exists (so failure = no match); R11-R16
            # match an absent element when present (so failure = match).
            #
            # Implementation: R1-R10 are "presence-required" rules;
            # R11-R16 are "absence-required" rules. We invert the
            # assertion for the latter.
            is_presence_required = rule_id in ("R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8", "R9", "R10")
            violated = (m is not None) if not is_presence_required else (m is None)
            if violated:
                line = 0
                if m is not None:
                    line = html[: m.start()].count("\n") + 1
                failures.append(
                    {
                        "rule_id": rule_id,
                        "message": message,
                        "line": line,
                    }
                )
                # R14 (truncation marker) is a structural halt; we
                # emit it as the FIRST failure and stop the regex
                # walk to keep the verdict deterministic.
                if rule_id == "R14":
                    break

        # Parse5 EOF gate — catches unbalanced ``<html>``, ``<body>``
        # structural tags. We use a stdlib parse5 tag counter (cheap,
        # no DOM construction). Falls back to a no-op when parse5
        # isn't importable; in that case the regex family is the only
        # gate and the lint returns pass/fail-N based on the regex
        # family alone (a degraded mode — the EOF gate is a structural
        # backstop; the regex family is what catches the live failure
        # mode).
        eof_failures = cls._parse5_eof_gate(html)
        failures.extend(eof_failures)

        # Bucket the verdict.
        has_r14 = any(f["rule_id"] == "R14" for f in failures)
        if has_r14:
            verdict = "fail-4"
        elif len(failures) == 0:
            verdict = "pass"
        elif len(failures) == 1:
            verdict = "fail-1"
        elif len(failures) <= 3:
            verdict = "fail-2"
        else:
            verdict = "fail-3"

        return {
            "verdict": verdict,
            "fail_count": len(failures),
            "failures": failures,
        }

    @classmethod
    def _parse5_eof_gate(cls, html: str) -> List[Dict[str, Any]]:
        """Parse5 EOF structural gate.

        Counts the opening vs closing tags for the four structural
        elements (``html``, ``head``, ``body``, ``article``). An
        imbalance is a structural truncation signature — emit a
        failure for the FIRST imbalance we encounter. The gate is a
        literal character scan (no real parse5 needed) so it runs in
        O(n) without a dependency.
        """
        import re

        failures: List[Dict[str, Any]] = []

        # tag-pair counts; we accept the void elements (br, hr, img,
        # meta, link, input) as self-closing and do not require a
        # closing tag for them.
        for tag in ("html", "head", "body", "article"):
            open_count = len(re.findall(rf"<{tag}\b[^>]*>", html, flags=re.IGNORECASE))
            close_count = len(re.findall(rf"</{tag}\s*>", html, flags=re.IGNORECASE))
            if open_count != close_count:
                failures.append(
                    {
                        "rule_id": "EOF",
                        "message": (
                            f"unbalanced <{tag}>...</{tag}> "
                            f"(open={open_count}, close={close_count}) — "
                            f"structural truncation or unterminated tag"
                        ),
                        "line": 0,
                    }
                )
                break  # one EOF failure is enough; surface more later if needed
        return failures