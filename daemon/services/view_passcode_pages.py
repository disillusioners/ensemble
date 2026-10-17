"""Public-lane page templates (P2 of live-view-passcode).

The five pages the public-lane router serves — interstitial,
failure, locked, 429 rate-limit, and the explicit
"secure-context required" notice — share the same shell:

* zero JavaScript (architecture-recommendation.md D11:129,
  phase2-plan.md D9 / T2.7)
* CSP ``default-src 'none'; script-src 'none'; style-src
  'nonce-...'; form-action 'self'; base-uri 'none'; frame-
  ancestors 'none'`` (the explicit ``script-src 'none'`` is
  the load-bearing line — even an inline ``<script>`` would
  be rejected)
* mobile viewport meta
* ``autocomplete=off`` on the passcode form
* NO reflection of the customer-entered code in any page body
* HTML-escaped root/rel path echoes
* headers on EVERY page response: CSP + ``X-Content-Type-Options:
  nosniff`` + ``Cache-Control: no-store`` + ``Referrer-Policy:
  no-referrer``

The locked page (HTTP 200, architecture-recommendation.md V3)
carries NO content, NO remaining-attempts count, NO passcode
form — the verify short-circuits at the lock check, so there
is no path back to the entry lane. The owner must call
``view_link(refresh=True)`` to recover.

The 429 page is a static notice — the limiter already
emitted the 429 envelope; the body is informational only (a
"too many attempts" message, never the strike count, never
a "remaining" hint that aids grinding).

The secure-context page is the explicit answer to the
plain-HTTP edge: ``Secure`` blocks cookie storage on direct
``http://LAN:9797`` (ADR D6:79 — never a redirect loop); the
body explains what to do (use the canonical https host).

Templates are inline-strings + ``str.format`` — no Jinja, no
build artifact, no ``frontend/`` coupling. The CSP nonce is
generated per-request by the caller (the nonce must be unique
per response — using a fixed nonce weakens the CSP to no
protection against replay).
"""

from __future__ import annotations

import html
import secrets
from typing import Optional


# Per-request CSP nonce (architecture-recommendation.md D11:129).
# The nonce is opaque, ~128 bits of entropy (16 bytes hex =
# 32 chars), and unique per response — a fixed nonce collapses
# the CSP to no protection against replay / precomputed
# payloads.
def new_csp_nonce() -> str:
    """Return a fresh 32-hex-char CSP nonce."""
    return secrets.token_hex(16)


# Shared hardening headers — the binding set on every
# public-lane page response (CSP + nosniff + no-store + no-
# referrer). The router ALSO sets ``X-Content-Type-Options`` /
# ``Cache-Control`` on the SUCCESS path (the served file), so
# the wrapper pages are the load-bearing carriers of the
# interstitial/failure/locked/429/secure-context headers.
def page_headers(*, csp_nonce: str) -> dict[str, str]:
    """Return the shared page-response headers."""
    return {
        # CSP: zero-JS is the strongest XSS posture. The
        # ``script-src 'none'`` line is the explicit ruling
        # (architecture-recommendation.md D11:129; supersedes
        # the prior ``style-src 'unsafe-inline'`` lean) — the
        # inline ``<style>`` block in this module is the only
        # reason we need the ``style-src 'nonce-...';`` line.
        "Content-Security-Policy": (
            "default-src 'none'; "
            "script-src 'none'; "
            f"style-src 'nonce-{csp_nonce}'; "
            "form-action 'self'; "
            "base-uri 'none'; "
            "frame-ancestors 'none'"
        ),
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "no-store",
        "Referrer-Policy": "no-referrer",
        "Content-Type": "text/html; charset=utf-8",
    }


# Shared minimal CSS — a single inline block, nonced. Mobile-
# responsive (max-width 480px, viewport-aware). No external
# resources (CSP forbids it).
_PAGE_CSS = """\
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; background: #fafafa; color: #222; font-family: system-ui, -apple-system, "Segoe UI", sans-serif; }
.wrap { max-width: 480px; margin: 0 auto; padding: 24px 16px; }
h1 { font-size: 20px; margin: 0 0 12px 0; }
p { font-size: 15px; line-height: 1.5; margin: 0 0 16px 0; }
label { display: block; font-size: 14px; margin: 0 0 6px 0; }
input[type="text"] {
  display: block; width: 100%; padding: 12px;
  font-size: 22px; letter-spacing: 4px; text-align: center;
  border: 1px solid #ccc; border-radius: 4px; background: #fff;
  font-family: ui-monospace, "SF Mono", Menlo, monospace;
}
button {
  display: block; width: 100%; margin-top: 12px; padding: 12px;
  font-size: 15px; background: #2563eb; color: #fff;
  border: 0; border-radius: 4px; cursor: pointer;
}
button:hover { background: #1d4ed8; }
.error { color: #b91c1c; margin-top: 12px; font-size: 14px; }
.note { color: #555; font-size: 13px; margin-top: 16px; }
"""


def _shell(
    *,
    title: str,
    body_inner: str,
    csp_nonce: str,
) -> str:
    """Wrap an inner HTML body in the shared page shell."""
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '  <meta charset="utf-8">\n'
        '  <meta name="viewport" '
        'content="width=device-width, initial-scale=1">\n'
        f"  <title>{html.escape(title)}</title>\n"
        f'  <style nonce="{html.escape(csp_nonce)}">{_PAGE_CSS}</style>\n'
        "</head>\n"
        "<body>\n"
        '  <div class="wrap">\n'
        f"{body_inner}\n"
        "  </div>\n"
        "</body>\n"
        "</html>\n"
    )


def render_interstitial(
    *,
    csp_nonce: str,
    root_name: str,
    rel_path: str,
    form_action: str,
) -> str:
    """Render the passcode-entry interstitial (GET hit, no cookie).

    The form POSTs to the same URL (architecture-recommendation.md
    D6:78 — same-URL POST; the form action IS the view URL).
    No reflection of the customer-entered code (architecture-
    recommendation.md D11:129 — even an echo would aid
    shoulder-surfing + replay).
    """
    body = (
        "<h1>Enter passcode</h1>\n"
        "<p>Enter the 6-character passcode to view this "
        "document.</p>\n"
        f'<form method="post" action="{html.escape(form_action)}" '
        'autocomplete="off">\n'
        '  <label for="code">Passcode</label>\n'
        '  <input type="text" id="code" name="code" '
        'inputmode="latin" '
        'pattern="[a-zA-Z0-9]{6}" '
        'maxlength="6" minlength="6" required '
        'autocomplete="off" autocapitalize="off" '
        'autocorrect="off" spellcheck="false">\n'
        '  <button type="submit">Submit</button>\n'
        "</form>\n"
        '<p class="note">'
        "Path: /"
        f"{html.escape(root_name)}/"
        f"{html.escape(rel_path)}"
        "</p>\n"
    )
    return _shell(
        title="Enter passcode",
        body_inner=body,
        csp_nonce=csp_nonce,
    )


def render_failure(
    *,
    csp_nonce: str,
    root_name: str,
    rel_path: str,
    form_action: str,
) -> str:
    """Render the interstitial with a generic error after a
    wrong code.

    Generic error — never "wrong code", never the strike
    count, never a reflection of the customer's submission
    (architecture-recommendation.md D11:131 + D2:40). The
    body re-displays the form so the customer can retry;
    the rate-limit interplay means a flooding attempt is
    already cut off upstream.
    """
    body = (
        "<h1>Enter passcode</h1>\n"
        "<p>Enter the 6-character passcode to view this "
        "document.</p>\n"
        '<p class="error">That code was not recognised.</p>\n'
        f'<form method="post" action="{html.escape(form_action)}" '
        'autocomplete="off">\n'
        '  <label for="code">Passcode</label>\n'
        '  <input type="text" id="code" name="code" '
        'inputmode="latin" '
        'pattern="[a-zA-Z0-9]{6}" '
        'maxlength="6" minlength="6" required '
        'autocomplete="off" autocapitalize="off" '
        'autocorrect="off" spellcheck="false">\n'
        '  <button type="submit">Submit</button>\n'
        "</form>\n"
        '<p class="note">'
        "Path: /"
        f"{html.escape(root_name)}/"
        f"{html.escape(rel_path)}"
        "</p>\n"
    )
    return _shell(
        title="Enter passcode",
        body_inner=body,
        csp_nonce=csp_nonce,
    )


def render_locked(*, csp_nonce: str) -> str:
    """Render the locked page (HTTP 200, architecture-
    recommendation.md V3 / D2 / R11).

    NO content, NO remaining-attempts count, NO passcode
    form (the verify short-circuits at the lock check — no
    path back to the entry lane). The owner must call
    ``view_link(refresh=True)`` to recover.
    """
    body = (
        "<h1>This view is locked</h1>\n"
        "<p>Too many incorrect passcode attempts. "
        "Please ask the person who shared this link for a "
        "fresh one.</p>\n"
    )
    return _shell(
        title="View locked",
        body_inner=body,
        csp_nonce=csp_nonce,
    )


def render_rate_limited(*, csp_nonce: str) -> str:
    """Render the 429 page (per-IP bucket exceeded).

    Static notice; the limiter already emitted the 429
    envelope. No remaining-attempts disclosure (would aid
    multi-IP grinding — architecture-recommendation.md D2:40).
    """
    body = (
        "<h1>Too many attempts</h1>\n"
        "<p>Please wait a moment before trying again.</p>\n"
    )
    return _shell(
        title="Too many attempts",
        body_inner=body,
        csp_nonce=csp_nonce,
    )


def render_secure_context_required(*, csp_nonce: str) -> str:
    """Render the explicit "secure context required" page
    (architecture-recommendation.md D6:79 — plain-HTTP edge).

    Never a redirect loop — a Secure cookie cannot be stored
    on plain http://LAN:9797; the body tells the customer
    to use the canonical https host. A 200 status with an
    actionable message (rather than a 4xx) keeps the
    page renderable on the unauthenticated lane.
    """
    body = (
        "<h1>Secure connection required</h1>\n"
        "<p>This link requires HTTPS. Please open it via "
        "the canonical secure URL.</p>\n"
    )
    return _shell(
        title="Secure connection required",
        body_inner=body,
        csp_nonce=csp_nonce,
    )


__all__ = [
    "new_csp_nonce",
    "page_headers",
    "render_failure",
    "render_interstitial",
    "render_locked",
    "render_rate_limited",
    "render_secure_context_required",
]