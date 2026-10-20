"""The LIVE board page — zero-JS, server-rendered HTML templates.

ari-tools Phase 3, deliverable B. This module owns the board page's
MARKUP ONLY. It consumes the Phase-3 :class:`BoardItemView` dataclass
and NOTHING else — it never imports ``daemon.services.board_service``
(the Phase-1 module) and never touches the DB. The adapter
(:mod:`daemon.services.board_view_service`) is the only place that
knows Phase 1's surface.

Build discipline (A-32 — a blocking defect if ignored)
------------------------------------------------------
The template is built by PLAIN STRING CONCATENATION and
``string.Template``. It is **NEVER** formatted with ``str.format``:
the page carries a ``<style>`` block and CSS contains literal ``{``
and ``}`` — ``str.format`` raises on the first brace ⇒ a 500 on EVERY
board GET. The ``_shell`` precedent is
``daemon/services/view_passcode_pages.py``.

Security posture
----------------
* The board page carries the STRICT CSP set — ``default-src 'none';
  script-src 'none'; style-src 'nonce-…'; form-action 'self';
  base-uri 'none'; frame-ancestors 'none'`` — via
  :func:`daemon.services.view_passcode_pages.page_headers`. The
  response headers come from that function ALONE (never merged with
  the live-views router's ``_HARDENING_HEADERS`` — the merge-order
  trap would let ``private, max-age=60`` beat ``no-store``).
* The page carries NO inline ``<script>`` and NO inline ``style=``
  attribute — the ONLY style is the nonce'd ``<style>`` block.
* The page carries NO external subresource — no ``https://``, no CDN,
  no font, no image. The board is a table.
* EVERY user-derived string passes through the ONE module-level
  escaper :func:`_esc` (the ``<pre>`` blocks go through
  :func:`_esc_multiline`, which delegates to it). NO user-derived
  value ever lands in a URL context: the ONLY URL context in the
  markup is the filter form's ``action=`` attribute, which carries
  the module CONSTANT :data:`BOARD_PUBLIC_PATH` — never a
  user-derived value — and is escaped with :func:`_esc`.
* The CSP nonce is minted PER REQUEST by the caller and lands in BOTH
  the response header and the ``<style nonce="…">`` attribute — the
  two must never drift (a mismatch ⇒ a silently unstyled board).

Bounds
------
The rendered body is capped at 2 MiB — counted in BYTES (the UTF-8
encoding length of every part, INCLUDING the shell/wrapper), never in
characters. The cap is enforced DURING assembly (a post-hoc slice
would cut mid-tag): the row builder stops adding rows when the next
one would exceed the ceiling and appends a VISIBLE notice. Never a
500, never a silent cut.
"""

from __future__ import annotations

import html
from typing import Any, Iterable

from .board_view_service import BoardItemView

#: The rendered-body ceiling (2 MiB). Enforced during assembly.
MAX_RENDERED_BYTES = 2 * 1024 * 1024

#: The statuses the filter form offers. Mirrors Phase 1's
#: ``BOARD_STATUSES`` plus the ``open`` convenience filter.
STATUS_OPTIONS: tuple[str, ...] = (
    "",
    "todo",
    "in_progress",
    "blocked",
    "done",
    "open",
)

#: The board's public URL — the form action and the page's canonical
#: self-reference. The board root is RESERVED and the rel path is
#: fixed, so this is a constant.
BOARD_PUBLIC_PATH = "/views/public/board/index.html"


def _esc(value: Any) -> str:
    """The ONE module-level escaper.

    ``None`` becomes ``""``; everything else is HTML-escaped with
    quotes on (so the value is safe inside an attribute too).
    """
    if value is None:
        return ""
    return html.escape(str(value), quote=True)


def _esc_multiline(value: Any) -> str:
    """Escape a value that lands inside a ``<pre>`` block.

    ``_esc`` neutralises ``</pre>`` as well, so the block cannot be
    closed early.
    """
    return _esc(value)


# ─────────────────────────────────────────────────────────────────
# The stylesheet — a single inline block, nonced.
# ─────────────────────────────────────────────────────────────────

_BOARD_CSS = """\
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; background: #fafafa; color: #222;
  font-family: system-ui, -apple-system, "Segoe UI", sans-serif; }
.wrap { max-width: 1100px; margin: 0 auto; padding: 24px 16px; }
h1 { font-size: 22px; margin: 0 0 8px 0; }
.meta { color: #555; font-size: 13px; margin: 0 0 16px 0; }
form.filters { display: flex; flex-wrap: wrap; gap: 12px; align-items: flex-end;
  margin: 0 0 16px 0; padding: 12px; border: 1px solid #e2e2e2;
  border-radius: 6px; background: #fff; }
form.filters label { display: block; font-size: 13px; margin: 0 0 4px 0; }
form.filters select, form.filters input[type="text"] {
  padding: 6px; font-size: 14px; border: 1px solid #ccc; border-radius: 4px;
  background: #fff; min-width: 180px; }
form.filters button { padding: 7px 14px; font-size: 14px; background: #2563eb;
  color: #fff; border: 0; border-radius: 4px; cursor: pointer; }
.counts { font-size: 14px; margin: 0 0 12px 0; }
table { width: 100%; border-collapse: collapse; background: #fff;
  border: 1px solid #e2e2e2; border-radius: 6px; }
th, td { text-align: left; padding: 8px 10px; border-bottom: 1px solid #eee;
  vertical-align: top; font-size: 14px; }
th { background: #f4f4f4; font-size: 12px; text-transform: uppercase;
  letter-spacing: 0.04em; color: #555; }
tr:last-child td { border-bottom: 0; }
.badge { display: inline-block; padding: 2px 8px; border-radius: 10px;
  font-size: 12px; background: #eee; color: #333; }
.badge.status-todo { background: #e0e7ff; }
.badge.status-in_progress { background: #fef3c7; }
.badge.status-blocked { background: #fee2e2; }
.badge.status-done { background: #dcfce7; }
.tags { color: #555; font-size: 13px; }
summary { cursor: pointer; font-weight: 600; }
pre { white-space: pre-wrap; word-break: break-word; background: #f7f7f7;
  padding: 8px; border-radius: 4px; font-size: 13px; }
ul.notes { margin: 8px 0 0 0; padding-left: 18px; }
ul.notes li { font-size: 13px; margin-bottom: 6px; }
.note { color: #555; font-size: 13px; margin: 16px 0 0 0; }
.warn { color: #b45309; font-size: 13px; margin: 4px 0; }
.empty { padding: 24px; text-align: center; color: #666; background: #fff;
  border: 1px solid #e2e2e2; border-radius: 6px; }
"""


def _shell(*, title: str, body_inner: str, csp_nonce: str) -> str:
    """Wrap an inner body in the board page shell.

    Plain concatenation — NEVER ``str.format`` (the CSS braces would
    explode it). The nonce is a SEPARATE placeholder spliced into the
    ``<style nonce>`` attribute; it is NEVER spliced into the CSS
    block itself.
    """
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '  <meta charset="utf-8">\n'
        '  <meta name="viewport" '
        'content="width=device-width, initial-scale=1">\n'
        "  <title>"
        + _esc(title)
        + "</title>\n"
        '  <style nonce="'
        + _esc(csp_nonce)
        + '">'
        + _BOARD_CSS
        + "</style>\n"
        "</head>\n"
        "<body>\n"
        '  <main class="wrap">\n'
        + body_inner
        + "\n  </main>\n"
        "</body>\n"
        "</html>\n"
    )


def _status_badge(status: str) -> str:
    """Render the status badge."""
    return (
        '<span class="badge status-'
        + _esc(status)
        + '">'
        + _esc(status)
        + "</span>"
    )


def _filter_form(
    *,
    active_status: str,
    active_project: str,
    active_tag: str,
    projects: Iterable[str],
) -> str:
    """Render the zero-JS GET filter form.

    The form action is the board URL itself and the method is ``get`` —
    a filtered page is a plain URL the customer can bookmark or share.
    """
    status_options = []
    for option in STATUS_OPTIONS:
        selected = " selected" if option == active_status else ""
        label = option if option else "All statuses"
        status_options.append(
            '<option value="'
            + _esc(option)
            + '"'
            + selected
            + ">"
            + _esc(label)
            + "</option>"
        )
    project_options = ['<option value="">All projects</option>']
    for project in projects:
        selected = " selected" if project == active_project else ""
        project_options.append(
            '<option value="'
            + _esc(project)
            + '"'
            + selected
            + ">"
            + _esc(project)
            + "</option>"
        )
    return (
        '<form class="filters" method="get" action="'
        + _esc(BOARD_PUBLIC_PATH)
        + '">\n'
        "  <div>\n"
        '    <label for="filter-status">Status</label>\n'
        '    <select id="filter-status" name="status">'
        + "".join(status_options)
        + "</select>\n"
        "  </div>\n"
        "  <div>\n"
        '    <label for="filter-project">Project</label>\n'
        '    <select id="filter-project" name="project">'
        + "".join(project_options)
        + "</select>\n"
        "  </div>\n"
        "  <div>\n"
        '    <label for="filter-tag">Tag</label>\n'
        '    <input type="text" id="filter-tag" name="tag" value="'
        + _esc(active_tag)
        + '">\n'
        "  </div>\n"
        '  <div><button type="submit">Filter</button></div>\n'
        "</form>\n"
    )


def _notes_block(item: BoardItemView) -> str:
    """Render one item's notes timeline (oldest-first, pure HTML).

    There is NO "notes unavailable" branch: after the W2 root fix a
    MISSING ``notes`` key means ZERO notes (a zero-note row is NOT an
    error), so the state this branch used to describe CANNOT occur.
    """
    if not item.notes:
        return ""
    parts = ['<ul class="notes">\n']
    for note in item.notes:
        parts.append(
            "  <li>"
            + "<strong>"
            + _esc(note.author_instance or "unknown")
            + "</strong> "
            + '<span class="meta">'
            + _esc(note.ts)
            + "</span><br>"
            + _esc_multiline(note.text)
            + ("<br><em>(note truncated)</em>" if note.truncated else "")
            + "</li>\n"
        )
    parts.append("</ul>\n")
    return "".join(parts)


def _item_row(item: BoardItemView) -> str:
    """Render one board item as a table row."""
    tags = ", ".join(item.tags) if item.tags else "—"
    notes_count = len(item.notes)
    body = ""
    if item.body_md:
        body = "<pre>" + _esc_multiline(item.body_md) + "</pre>\n"
    if item.body_truncated:
        body += (
            '<p class="warn">(body truncated at the 64 KiB cap)</p>\n'
        )
    notes = _notes_block(item)
    if item.notes_truncated:
        notes += (
            '<p class="warn">(notes truncated at the 20-note cap)</p>\n'
        )
    return (
        "<tr>"
        "<td>"
        + _status_badge(item.status)
        + "</td>"
        "<td>"
        + _esc(item.priority if item.priority is not None else "—")
        + "</td>"
        "<td>"
        "<details>"
        "<summary>"
        + _esc(item.title)
        + "</summary>"
        '<p class="meta">'
        + _esc(item.id)
        + "</p>\n"
        + body
        + notes
        + "</details>"
        "</td>"
        '<td class="tags">'
        + _esc(tags)
        + "</td>"
        "<td>"
        + _esc(item.project_id or "—")
        + "</td>"
        "<td>"
        + _esc(item.due_at or "—")
        + "</td>"
        "<td>"
        + _esc(item.updated_at or "—")
        + "</td>"
        "<td>"
        + _esc(notes_count)
        + "</td>"
        "</tr>\n"
    )


#: The page's footer note — the D11 "filters are a VIEW concern, not
#: access control" disclosure. A module constant so the render budget
#: can RESERVE its bytes up-front: the FINAL document must never
#: exceed the ceiling, and the footer is part of that document.
_FOOTER_NOTE = (
    '<p class="note">Filters are a VIEW concern, not access control — a '
    "passcode holder sees the whole board; a filter only narrows what is "
    "displayed.</p>\n"
)


def _format_bytes(value: int) -> str:
    """Render a byte count for the ceiling disclosure.

    ``2 * 1024 * 1024`` → ``2 MiB`` (byte-identical to the historical
    wording); any other ceiling names ITSELF, so the notice never
    claims a cap the render did not enforce.
    """
    if value >= 1024 * 1024 and value % (1024 * 1024) == 0:
        return str(value // (1024 * 1024)) + " MiB"
    if value >= 1024 and value % 1024 == 0:
        return str(value // 1024) + " KiB"
    return str(value) + " bytes"


def _truncation_notice(budget: int) -> str:
    """The VISIBLE truncation notice — it names the ACTUAL ceiling.

    The notice is assembled BEFORE the rows are added (its bytes are
    RESERVED from the budget), so the ceiling it quotes is the one the
    render really enforced.
    """
    return (
        '<p class="warn">Output truncated at the '
        + _format_bytes(budget)
        + " render ceiling — narrow the filters to see the remaining "
        "items.</p>\n"
    )


def render_board(
    *,
    csp_nonce: str,
    items: list[BoardItemView],
    filter_options: dict,
    active_filters: dict,
    counts: dict,
    generated_at: str,
    shown: int = 0,
    total: int = 0,
    truncated: bool = False,
    max_bytes: int = MAX_RENDERED_BYTES,
) -> str:
    """Render the live board page.

    Args:
        csp_nonce: The per-request CSP nonce — the SAME string the
            response header carries (``style-src 'nonce-…'``). The
            two must never drift.
        items: The rows to render (already bounded by the adapter).
        filter_options: ``{"projects": [...], "tags": [...], …}`` from
            the adapter.
        active_filters: ``{"status": …, "project": …, "tag": …}`` — the
            echoed filter values.
        counts: ``{"total": N, "shown": N, …}`` — the status counts.
        generated_at: The render timestamp.
        shown: How many rows are rendered.
        total: How many rows match the filters. When ``truncated`` is
            True this is the FETCH PAGE SIZE — a LOWER BOUND on the
            match count, not the match count itself — and the
            disclosure says so.
        truncated: True when more rows match beyond the fetch bound.
        max_bytes: The rendered-body ceiling.

    Returns:
        The complete HTML document.
    """
    active_status = _as_filter(active_filters.get("status"))
    active_project = _as_filter(active_filters.get("project"))
    active_tag = _as_filter(active_filters.get("tag"))

    parts: list[str] = []
    parts.append("<h1>Work board</h1>\n")
    parts.append(
        '<p class="meta">Rendered fresh from the database at '
        + _esc(generated_at)
        + "</p>\n"
    )
    parts.append(
        _filter_form(
            active_status=active_status,
            active_project=active_project,
            active_tag=active_tag,
            projects=filter_options.get("projects", []) or [],
        )
    )

    # The truncation disclosure — VISIBLE, never silent (A-41 / R47).
    #
    # TRUTHFUL IN BOTH CASES. When the fetch bound was NOT hit,
    # ``total`` IS the exact match count and the wording is the
    # historical one (byte-identical). When the bound WAS hit,
    # ``total`` is the FETCH PAGE SIZE — Phase 1's ``count`` is the
    # page size, NOT the number of matching rows — so the count is
    # marked with a ``+`` and the caveat names the bound. Without the
    # ``+``, "Showing 50 of 200 items" reads as a TOTAL while the true
    # match count is larger.
    if truncated:
        disclosure = (
            "Showing "
            + _esc(shown)
            + " of "
            + _esc(total)
            + "+ items (fetch bound — more items match these filters"
            " — narrow them to see the rest)"
        )
    else:
        disclosure = "Showing " + _esc(shown) + " of " + _esc(total) + " items"
    parts.append('<p class="counts">' + disclosure + "</p>\n")

    # The status-count row.
    #
    # A1 (council): the chips describe the RENDERED set — the rows
    # that survived the byte ceiling — NOT the fetched set. The
    # ``rendered:`` label says so; without it the chips read as a
    # board-wide total.
    status_counts = counts.get("statuses", {}) or {}
    if status_counts:
        chips = " · ".join(
            _esc(key) + ": " + _esc(value)
            for key, value in sorted(status_counts.items())
        )
        parts.append('<p class="counts">rendered: ' + chips + "</p>\n")
    if filter_options.get("truncated"):
        parts.append(
            '<p class="warn">The filter-option set is capped at '
            + _esc(filter_options.get("applied_limit", 0))
            + " values (the board list has no pagination cursor) — narrow the "
            "filters to reach the rest.</p>\n"
        )

    if not items:
        parts.append(
            '<p class="empty">No board items match this view.</p>\n'
        )
        return _shell(
            title="Work board",
            body_inner="".join(parts),
            csp_nonce=csp_nonce,
        )

    # The table — assembled under the byte ceiling.
    #
    # W1 (council): the ceiling is counted in BYTES, not CHARACTERS.
    # The old counter summed ``len(part)`` — characters — so a CJK
    # board measured 2,071,261 characters was actually ~6.1 MB: 2.9x
    # the claimed 2 MiB cap. Every part is counted by its UTF-8
    # encoding length, and the SHELL (the wrapper + the CSS + the
    # nonce) is SEEDED into the budget — the shell now counts toward
    # the cap (this also closes the ~2 KB wrapper overshoot).
    table: list[str] = []
    table.append("<table>\n")
    table.append(
        "<thead><tr>"
        "<th>Status</th><th>Priority</th><th>Title</th><th>Tags</th>"
        "<th>Project</th><th>Due</th><th>Updated</th><th>Notes</th>"
        "</tr></thead>\n"
    )
    table.append("<tbody>\n")
    rendered = 0
    truncated_by_bytes = False
    budget = max(0, int(max_bytes))
    # The SHELL overhead — the wrapper rendered with an EMPTY body:
    # the exact bytes the final document spends on everything but the
    # body itself.
    shell_bytes = len(
        _shell(
            title="Work board", body_inner="", csp_nonce=csp_nonce
        ).encode("utf-8")
    )
    used = shell_bytes + sum(len(part.encode("utf-8")) for part in parts)
    used += sum(len(part.encode("utf-8")) for part in table)
    # The bytes that come AFTER the rows — the closing table tags, the
    # truncation notice and the footer note — are RESERVED up-front, so
    # the FINAL document can never exceed the ceiling.
    reserve = sum(
        len(part.encode("utf-8"))
        for part in (
            "</tbody>\n",
            "</table>\n",
            _truncation_notice(budget),
            _FOOTER_NOTE,
        )
    )
    for item in items:
        row = _item_row(item)
        row_bytes = len(row.encode("utf-8"))
        if used + row_bytes + reserve > budget:
            truncated_by_bytes = True
            break
        table.append(row)
        used += row_bytes
        rendered += 1
    table.append("</tbody>\n")
    table.append("</table>\n")
    parts.extend(table)

    if truncated_by_bytes:
        parts.append(_truncation_notice(budget))

    parts.append(_FOOTER_NOTE)

    return _shell(
        title="Work board",
        body_inner="".join(parts),
        csp_nonce=csp_nonce,
    )


def render_unavailable(*, csp_nonce: str) -> str:
    """Render the "board backend unavailable" notice.

    HTTP 200, zero-JS, no partial data — the board backend is not
    reachable, so the page says so plainly instead of 500-ing. No
    disclosure of the failure detail (the PB-F1 leak class).
    """
    body = (
        "<h1>Work board</h1>\n"
        '<p class="warn">The board backend is currently unavailable — '
        "the board could not be read.</p>\n"
    )
    return _shell(
        title="Work board",
        body_inner=body,
        csp_nonce=csp_nonce,
    )


def _as_filter(value: Any) -> str:
    """Coerce an echoed filter value to text (``None`` → ``""``)."""
    if value is None:
        return ""
    return str(value)


__all__ = [
    "BOARD_PUBLIC_PATH",
    "MAX_RENDERED_BYTES",
    "STATUS_OPTIONS",
    "render_board",
    "render_unavailable",
]
