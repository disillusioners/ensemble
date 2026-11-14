"""Unit tests for the live-view media streaming lane (D3).

Pins the production behaviour shipped in commit c6a9a80fb:

* Full-file GET of a media extension returns 200 with the
  extension's media Content-Type, the real Content-Length, and
  ``Accept-Ranges: bytes`` (no ``Content-Range``).
* Ranged GET returns 206 with a byte-exact slice + the
  matching ``Content-Range``.
* HEAD media returns the real size + ``Accept-Ranges``.
* Over-media-cap returns 413 with the named body.
* Non-media over 32 MiB returns the uniform 404.
* Traversal blocked for media paths.
* Public lane: media without a passcode row → 404; with a
  valid grant → 200/206 byte-identical to the direct lane.
* Non-media no-regression.

Mirrors the fixtures in ``test_live_views_markdown.py`` and
``test_live_views_public_lane.py``.

Size rationale: one module by design — public-lane byte-identity
pins sit beside the direct-lane fixtures they mirror.
"""

from __future__ import annotations

import hmac
import os
import pathlib

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from daemon.config import LiveViewsConfig, LiveViewsRootConfig
from daemon.routers.live_views import build_router
from daemon.services.live_views import (
    MAX_SERVED_BYTES,
    LiveViewsService,
    ServedFileTooLargeError,
    is_media_extension,
    mime_for_extension,
)
from daemon.services.view_passcode_service import (
    GRANT_COOKIE_NAME,
    ViewPasscodeCipher,
    ViewPasscodeService,
    VerifyRateLimiter,
    generate_passcode,
    issue_grant_cookie,
)


# ---------------------------------------------------------------------------
# Direct lane fixtures
# ---------------------------------------------------------------------------


def _service_with_media_allowed(
    workdir: pathlib.Path, *, max_media_bytes: int | None = None
) -> LiveViewsService:
    cfg = LiveViewsConfig()
    if max_media_bytes is not None:
        cfg.max_media_bytes = max_media_bytes
    allowed = [
        "html",
        "png",
        "mp4",
        "webm",
        "mp3",
        "md",
    ]
    cfg.roots["designer-artifact"] = LiveViewsRootConfig(
        type="project_scoped",
        path=".agents/shared/planning",
        required_rel_subpath=["design", "mockups"],
        allowed_extensions=allowed,
    )
    return LiveViewsService(
        config=cfg,
        project_workdir_by_shortname_resolver=lambda shortname: (
            str(workdir) if shortname == "ens" else None
        ),
    )


def _client_with_workdir(
    workdir: pathlib.Path, *, max_media_bytes: int | None = None
) -> TestClient:
    service = _service_with_media_allowed(workdir, max_media_bytes=max_media_bytes)
    app = FastAPI()
    app.include_router(build_router())
    app.state.live_views_service = service
    return TestClient(app)


@pytest.fixture
def media_root_dir(tmp_path: pathlib.Path) -> pathlib.Path:
    workdir = tmp_path / "project"
    mockup = (
        workdir
        / ".agents"
        / "shared"
        / "planning"
        / "feat"
        / "design"
        / "mockups"
    )
    mockup.mkdir(parents=True)
    (mockup / "clip.mp4").write_bytes(b"\x00" * 1000 + b"MP4DATA" * 10)
    data = (mockup / "clip.mp4").read_bytes()
    (mockup / "audio.mp3").write_bytes(b"ID3" + b"\x00" * 500)
    (mockup / "video.webm").write_bytes(b"\x1a\x45\xdf\xa3" + b"\x00" * 200)
    (mockup / "landing.html").write_text("<h1>Landing</h1>")
    (mockup / "image.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 50)
    return workdir


def _url(name: str) -> str:
    return f"/views/designer-artifact/ens/feat/design/mockups/{name}"


# ---------------------------------------------------------------------------
# Public lane fixtures (copied + media-allowed)
# ---------------------------------------------------------------------------


class _InMemoryPasscodeStore:
    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], object] = {}

    def upsert(self, root_name, rel_path, *, wrapped_passcode, generation=1,
               failed_attempts=0, locked=False):
        from dataclasses import dataclass

        @dataclass
        class _Row:
            id: str
            root_name: str
            rel_path: str
            wrapped_passcode: bytes
            generation: int
            failed_attempts: int
            locked: bool
            created_at: str
            last_attempt_at: object = None

        key = (root_name, rel_path)
        existing = self.rows.get(key)
        if existing is None:
            row = _Row(
                id=f"row-{len(self.rows)+1}",
                root_name=root_name,
                rel_path=rel_path,
                wrapped_passcode=wrapped_passcode,
                generation=generation,
                failed_attempts=failed_attempts,
                locked=locked,
                created_at="2026-10-17T00:00:00Z",
            )
            self.rows[key] = row
        else:
            existing.wrapped_passcode = wrapped_passcode
            existing.generation = generation
            existing.failed_attempts = failed_attempts
            existing.locked = locked
            row = existing
        return row

    def get_row(self, root_name, rel_path):
        return self.rows.get((root_name, rel_path))


class _InMemoryStorePort:
    def __init__(self, store, cipher):
        self._store = store
        self._cipher = cipher

    def get_row(self, root_name, rel_path):
        return self._store.get_row(root_name, rel_path)

    def mint(self, root_name, rel_path):
        plaintext = generate_passcode()
        wrapped = self._cipher.wrap(plaintext.encode("ascii"))
        row = self._store.upsert(
            root_name, rel_path, wrapped_passcode=wrapped, generation=1
        )
        return plaintext, row

    def verify(self, root_name, rel_path, candidate):
        row = self._store.get_row(root_name, rel_path)
        if row is None:
            return False, None
        try:
            stored = self._cipher.unwrap(row.wrapped_passcode)
        except Exception:
            return False, row
        if hmac.compare_digest(stored, candidate.encode("ascii")):
            return True, row
        return False, row

    def record_failure(self, root_name, rel_path):
        row = self._store.get_row(root_name, rel_path)
        if row is not None:
            row.failed_attempts = (row.failed_attempts or 0) + 1
            if row.failed_attempts >= 10:
                row.locked = True

    def refresh(self, root_name, rel_path):
        row = self._store.get_row(root_name, rel_path)
        if row is not None:
            row.generation = (row.generation or 1) + 1

    def append_audit(self, *a, **k):
        return None


_TEST_FERNET_KEY = b"Z6gPqrtSzfIcd1aVvbiGoeY9N-99lxx7dx1lzh4fDoE="


@pytest.fixture
def pub_cipher() -> ViewPasscodeCipher:
    return ViewPasscodeCipher(_TEST_FERNET_KEY)


@pytest.fixture
def pub_store() -> _InMemoryPasscodeStore:
    return _InMemoryPasscodeStore()


@pytest.fixture
def pub_passcode_service(pub_cipher, pub_store) -> ViewPasscodeService:
    service = ViewPasscodeService.__new__(ViewPasscodeService)
    service._engine = None
    service._cipher = pub_cipher
    service._store = _InMemoryStorePort(pub_store, pub_cipher)
    return service


@pytest.fixture
def pub_client(
    media_root_dir: pathlib.Path,
    pub_cipher: ViewPasscodeCipher,
    pub_passcode_service: ViewPasscodeService,
) -> TestClient:
    cfg = LiveViewsConfig()
    workdir = media_root_dir
    cfg.roots["planning"] = LiveViewsRootConfig(
        type="project_scoped",
        path=".agents/shared/planning",
        required_rel_subpath=["design", "mockups"],
        allowed_extensions=["html", "mp4", "webm", "mp3", "png", "md"],
    )
    service = LiveViewsService(
        config=cfg,
        project_workdir_by_shortname_resolver=lambda shortname: (
            str(workdir) if shortname == "ens" else None
        ),
    )
    app = FastAPI()
    app.include_router(build_router())
    app.state.live_views_service = service
    app.state.view_passcode_service = pub_passcode_service
    app.state.view_passcode_cipher = pub_cipher
    app.state.view_passcode_grant_ttl_seconds = 86400
    app.state.verify_rate_limiter = VerifyRateLimiter(capacity=50, refill_per_minute=50)
    return TestClient(app)


# ===========================================================================
# Group 1 — Full-file GET
# ===========================================================================


class TestFullFileGet:
    def test_mp4_full(self, media_root_dir):
        c = _client_with_workdir(media_root_dir)
        data = (media_root_dir / ".agents/shared/planning/feat/design/mockups/clip.mp4").read_bytes()
        resp = c.get(_url("clip.mp4"))
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "video/mp4"
        assert resp.headers["content-length"] == str(len(data))
        assert resp.headers.get("accept-ranges") == "bytes"
        assert "content-range" not in resp.headers
        # F6: nosniff on the 200 media response (MIME-sniff XSS guard).
        assert resp.headers["x-content-type-options"] == "nosniff"
        assert resp.content == data

    def test_webm_full(self, media_root_dir):
        c = _client_with_workdir(media_root_dir)
        data = (media_root_dir / ".agents/shared/planning/feat/design/mockups/video.webm").read_bytes()
        resp = c.get(_url("video.webm"))
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "video/webm"
        assert resp.headers["content-length"] == str(len(data))
        assert resp.headers.get("accept-ranges") == "bytes"

    def test_mp3_full(self, media_root_dir):
        c = _client_with_workdir(media_root_dir)
        data = (media_root_dir / ".agents/shared/planning/feat/design/mockups/audio.mp3").read_bytes()
        resp = c.get(_url("audio.mp3"))
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "audio/mpeg"
        assert resp.headers["content-length"] == str(len(data))
        assert resp.headers.get("accept-ranges") == "bytes"


# ===========================================================================
# Group 2 — Ranged GET
# ===========================================================================


class TestRangedGet:
    def _ctx(self, media_root_dir: pathlib.Path) -> tuple[TestClient, bytes, int, str]:
        c = _client_with_workdir(media_root_dir)
        data = (media_root_dir / ".agents/shared/planning/feat/design/mockups/clip.mp4").read_bytes()
        size = len(data)
        url = _url("clip.mp4")
        return c, data, size, url

    def test_first_chunk(self, media_root_dir):
        c, data, size, url = self._ctx(media_root_dir)
        resp = c.get(url, headers={"Range": "bytes=0-99"})
        assert resp.status_code == 206
        assert resp.headers["content-range"] == f"bytes 0-99/{size}"
        assert resp.headers["content-length"] == "100"
        # F6: nosniff rides the 206 partial path too (FileResponse
        # inherits the caller's _HARDENING_HEADERS dict).
        assert resp.headers["x-content-type-options"] == "nosniff"
        assert resp.content == data[0:100]

    def test_mid_chunk(self, media_root_dir):
        c, data, size, url = self._ctx(media_root_dir)
        start = min(100, size // 2)
        end = start + 50
        if end >= size:
            end = size - 1
        resp = c.get(url, headers={"Range": f"bytes={start}-{end}"})
        assert resp.status_code == 206
        assert resp.headers["content-range"] == f"bytes {start}-{end}/{size}"
        assert resp.content == data[start:end+1]

    def test_suffix_range(self, media_root_dir):
        c, data, size, url = self._ctx(media_root_dir)
        resp = c.get(url, headers={"Range": "bytes=-100"})
        assert resp.status_code == 206
        assert resp.headers["content-range"] == f"bytes {size-100}-{size-1}/{size}"
        assert resp.content == data[-100:]

    @pytest.mark.parametrize("delta", [1, 999])
    def test_suffix_larger_than_file_gets_compliant_416(
        self, media_root_dir, delta
    ):
        """An over-long suffix is unsatisfiable — answer it OURSELVES.

        REGRESSION PIN (residual #2 of the multi-range fix). Starlette's
        ``_parse_ranges`` rewrites ``bytes=-N`` to ``start = size - N``;
        for ``N > size`` that start is NEGATIVE, so Starlette's own
        ``0 <= start`` guard raises ``RangeNotSatisfiable`` and its
        handler answers ``Content-Range: */<size>`` — WITHOUT the
        RFC-7233 ``bytes `` token. The pre-flight now treats
        ``N > size`` as unsatisfiable and emits the compliant header.

        Mutation that flips this RED: revert the suffix predicate in
        ``_media_preflight`` to ``unsatisfiable = int(last) <= 0`` —
        both suffixes then fall through to Starlette and the header
        asserts ``'*/1070' != 'bytes */1070'``.
        """
        c, data, size, url = self._ctx(media_root_dir)
        resp = c.get(url, headers={"Range": f"bytes=-{size + delta}"})
        assert resp.status_code == 416
        assert resp.headers["content-range"] == f"bytes */{size}"
        assert resp.headers["content-length"] == "0"

    def test_suffix_equal_to_size_is_satisfiable(self, media_root_dir):
        """``bytes=-<size>`` means "the whole file" — 206, NOT 416.

        BOUNDARY GUARD for the over-long-suffix rule above: the
        predicate must be strict ``>``. A ``>=`` would turn this
        legitimate whole-file suffix into a 416.

        Mutation that flips this RED: change ``int(last) > size`` to
        ``int(last) >= size`` in ``_media_preflight`` — the response
        becomes 416 and the 206/body assertions fail.
        """
        c, data, size, url = self._ctx(media_root_dir)
        resp = c.get(url, headers={"Range": f"bytes=-{size}"})
        assert resp.status_code == 206
        assert resp.headers["content-range"] == f"bytes 0-{size-1}/{size}"
        assert resp.content == data

    def test_open_ended(self, media_root_dir):
        c, data, size, url = self._ctx(media_root_dir)
        resp = c.get(url, headers={"Range": f"bytes={size-40}-"})
        assert resp.status_code == 206
        assert resp.headers["content-length"] == "40"
        assert resp.content == data[size-40:]

    def test_last_chunk(self, media_root_dir):
        c, data, size, url = self._ctx(media_root_dir)
        resp = c.get(url, headers={"Range": f"bytes={size-100}-{size-1}"})
        assert resp.status_code == 206
        assert resp.headers["content-range"] == f"bytes {size-100}-{size-1}/{size}"
        assert resp.content == data[size-100:]

    def test_unsatisfiable(self, media_root_dir):
        c, data, size, url = self._ctx(media_root_dir)
        resp = c.get(url, headers={"Range": f"bytes={size}-"})
        assert resp.status_code == 416
        assert resp.headers["content-range"] == f"bytes */{size}"

    @pytest.mark.parametrize(
        "range_value", ["bytes=9999-,9998-", "bytes=0-99,99999-"]
    )
    def test_multi_range_unsatisfiable_content_range_token(
        self, media_root_dir, range_value
    ):
        """RFC 7233 §4.4: the 416 MUST carry ``bytes */<size>``.

        REGRESSION PIN for the multi-range hole. The router's
        pre-emption used to match only a SINGLE ``^bytes=(\\d*)-(\\d*)$``
        form; a comma list missed the regex, fell through to
        ``FileResponse``, and Starlette's ``RangeNotSatisfiable``
        handler answered ``Content-Range: */<size>`` — WITHOUT the
        ``bytes `` token. The pre-flight now walks every part of the
        list.

        Mutation that flips this RED: restore the single-range-only
        match (``re.match(r"^bytes=(\\d*)-(\\d*)$", value)``) in
        ``_media_preflight`` — both ranges then fall through to
        Starlette and the header asserts ``'*/1070' != 'bytes */1070'``.
        """
        c, data, size, url = self._ctx(media_root_dir)
        resp = c.get(url, headers={"Range": range_value})
        assert resp.status_code == 416
        assert resp.headers["content-range"] == f"bytes */{size}"
        assert resp.headers["content-length"] == "0"

    def test_multi_range_partially_malformed_defers_to_starlette(
        self, media_root_dir
    ):
        """An unparsable part is NOT ours to judge — defer to Starlette.

        ``garbage`` is not a byte-range part, so ``_media_preflight``
        returns ``None`` (it only answers PARSABLE-but-unsatisfiable
        ranges) and Starlette's ``_parse_ranges`` drops the offending
        part, leaving the single valid ``0-99`` → 206.

        (Named ``..._defers_to_400`` in the original brief, which
        expected a 400; Starlette 0.52.1 IGNORES unparsable parts
        rather than rejecting the header, so the truthful name is
        kept here. A header whose UNIT is wrong — ``items=0-5`` — is
        the shape that yields Starlette's 400, pinned by
        ``test_malformed_units``.)

        Mutation that flips this RED: make the helper treat a
        non-matching part as unsatisfiable (``return`` the 416
        instead of ``None`` at the ``match is None`` branch) — the
        response becomes 416 and this assertion fails.
        """
        c, data, size, url = self._ctx(media_root_dir)
        resp = c.get(url, headers={"Range": "bytes=0-99,garbage"})
        assert resp.status_code == 206
        assert resp.headers["content-range"] == f"bytes 0-99/{size}"
        assert resp.content == data[0:100]

    def test_dash_part_is_ignored_not_416(self, media_root_dir):
        """A bare ``-`` part is IGNORED (Starlette skip list), not 416.

        Starlette's ``_parse_ranges`` skips ``not part or part == "-"``,
        so ``bytes=0-99,-`` and ``bytes=-,0-99`` BOTH serve the single
        valid ``0-99`` range as 206. The pre-flight must mirror that
        skip list — its old ``first == "" and last == ""`` branch
        treated the bare ``-`` as unsatisfiable and answered 416,
        contradicting Starlette and the helper's own
        defer-what-we-cannot-classify contract.

        Mutation that flips this RED: re-add the
        ``first == "" and last == "" → _unsatisfiable_416(size)``
        branch (i.e. drop the ``part == "-"`` skip) — both shapes
        become 416.
        """
        c, data, size, url = self._ctx(media_root_dir)
        for header in ("bytes=0-99,-", "bytes=-,0-99"):
            resp = c.get(url, headers={"Range": header})
            assert resp.status_code == 206, header
            assert resp.headers["content-range"] == f"bytes 0-99/{size}", header
            assert resp.content == data[0:100], header

    def test_reversed_bounds_defers_to_400(self, media_root_dir):
        """Reversed bounds inside a list are Starlette's 400, not our 416.

        ``bytes=0-99,5-3`` — the second part is reversed, which
        Starlette's ``_parse_range_header`` classifies as
        ``MalformedRangeHeader`` → 400 for the whole request. The
        pre-flight only answers provably-UNSATISFIABLE shapes.

        Mutation that flips this RED: restore the
        ``elif last != "" and int(last) < int(first): return
        _unsatisfiable_416(size)`` branch — the response becomes 416
        with a ``Content-Range`` header.
        """
        c, data, size, url = self._ctx(media_root_dir)
        resp = c.get(url, headers={"Range": "bytes=0-99,5-3"})
        assert resp.status_code == 400
        assert "content-range" not in resp.headers

    def test_reversed_past_eof_gets_compliant_416(self, media_root_dir):
        """Reversed AND past-EOF → OUR compliant 416, not Starlette's.

        ``bytes=5000-3`` / ``bytes=1024-3`` are BOTH reversed (``3 <
        start``) AND start-past-EOF. Past-EOF is the stronger, provable
        classification and must be checked FIRST: when the reversed
        branch won, we deferred, Starlette answered 416 with the
        token-less ``Content-Range: */<size>`` — the exact RFC-7233
        §4.4 violation this line of fixes exists to eliminate.

        Starlette's own guard order agrees on the STATUS (its
        ``0 <= start < size`` check runs before its ``start > end``
        check), it just omits the ``bytes `` token.

        Mutation that flips this RED: move the
        ``elif last != "" and int(last) < int(first)`` branch back
        ABOVE the ``elif int(first) >= size`` branch — both shapes
        fall through to Starlette and the header becomes ``'*/1070'``.
        """
        c, data, size, url = self._ctx(media_root_dir)
        # The dispatcher's repro literals (``bytes=5000-3`` /
        # ``bytes=1024-3``) were measured on a 1024-byte file; this
        # fixture's clip.mp4 is 1070 bytes, so the SAME two shapes are
        # expressed size-relative: "far past EOF" and "exactly at EOF",
        # both of which are also reversed (end ``3`` < start).
        for header in (f"bytes={size + 1000}-3", f"bytes={size}-3"):
            resp = c.get(url, headers={"Range": header})
            assert resp.status_code == 416, header
            assert resp.headers["content-range"] == f"bytes */{size}", header
            assert resp.headers["content-length"] == "0", header

    def test_reversed_within_range_still_defers_to_400(self, media_root_dir):
        """BOUNDARY: reversed INSIDE range must stay Starlette's 400.

        ``bytes=1023-3`` has a start BELOW EOF, so it is only reversed
        — ``MalformedRangeHeader`` → 400. This is the pin that proves
        the past-EOF-before-reversed reorder did not over-reach: if the
        ``last < first`` defer is dropped (or the past-EOF branch
        widened to ``>` / ``!=``), this becomes a 416.

        Mutation that flips this RED (verified): widen the past-EOF
        branch to swallow reversed pairs, i.e.
        ``elif int(first) >= size or (last != "" and int(last) < int(first)):``
        — ``bytes=1023-3`` then becomes a 416.
        """
        c, data, size, url = self._ctx(media_root_dir)
        resp = c.get(url, headers={"Range": f"bytes={size - 1}-3"})
        assert resp.status_code == 400
        assert "content-range" not in resp.headers

    def test_multi_range_two(self, media_root_dir):
        c, data, size, url = self._ctx(media_root_dir)
        resp = c.get(url, headers={"Range": "bytes=0-1,4-5"})
        assert resp.status_code == 206
        assert "multipart/byteranges" in resp.headers["content-range"]
        assert "boundary=" in resp.headers["content-range"]

    def test_multi_range_large(self, media_root_dir):
        c, data, size, url = self._ctx(media_root_dir)
        resp = c.get(url, headers={"Range": "bytes=0-99,200-299"})
        assert resp.status_code == 206
        assert "multipart/byteranges" in resp.headers["content-range"]

    def test_malformed_reverse(self, media_root_dir):
        """Reversed bounds are Starlette's 400 — NOT our 416.

        ``bytes=5-3`` parses as ``start=5, end=4``; Starlette's
        ``_parse_range_header`` raises ``MalformedRangeHeader`` for
        ``start > end`` and answers 400. The media pre-flight only
        answers PROVABLY UNsatisfiable shapes (suffix <= 0 or longer
        than the file, start at/past EOF), so it defers here.

        Behaviour change (review fix round, F1): this pin previously
        asserted 416 + ``bytes */{size}`` — the pre-flight's old
        ``last < first → unsatisfiable`` branch contradicted both
        Starlette and our own defer-what-we-cannot-classify contract.
        It emitted no ``Content-Range`` at all (a plaintext 400).

        Mutation that flips this RED: re-add
        ``elif last != "" and int(last) < int(first): return
        _unsatisfiable_416(size)`` in ``_media_preflight``.
        """
        c, data, size, url = self._ctx(media_root_dir)
        resp = c.get(url, headers={"Range": "bytes=5-3"})
        assert resp.status_code == 400
        assert "content-range" not in resp.headers

    def test_malformed_garbage(self, media_root_dir):
        c, data, size, url = self._ctx(media_root_dir)
        resp = c.get(url, headers={"Range": "bytes=abc-def"})
        assert resp.status_code == 400

    def test_malformed_units(self, media_root_dir):
        c, data, size, url = self._ctx(media_root_dir)
        resp = c.get(url, headers={"Range": "items=0-5"})
        assert resp.status_code == 400

    def test_negative_control_206_body_less_than_full(self, media_root_dir):
        c, data, size, url = self._ctx(media_root_dir)
        resp = c.get(url, headers={"Range": "bytes=0-99"})
        assert resp.status_code == 206
        assert len(resp.content) < len(data)


# ===========================================================================
# Group 3 — HEAD media
# ===========================================================================


class TestHeadMedia:
    def test_direct_head(self, media_root_dir):
        c = _client_with_workdir(media_root_dir)
        data = (media_root_dir / ".agents/shared/planning/feat/design/mockups/clip.mp4").read_bytes()
        resp = c.head(_url("clip.mp4"))
        assert resp.status_code == 200
        assert resp.headers["content-length"] == str(len(data))
        assert resp.headers.get("accept-ranges") == "bytes"
        assert resp.content == b""

    def test_public_head_granted(self, pub_client, pub_store, pub_cipher, media_root_dir):
        plaintext = generate_passcode()
        wrapped = pub_cipher.wrap(plaintext.encode("ascii"))
        pub_store.upsert("planning", "ens/feat/design/mockups/clip.mp4", wrapped_passcode=wrapped)
        token = issue_grant_cookie(
            cipher=pub_cipher,
            root_name="planning",
            rel_path="ens/feat/design/mockups/clip.mp4",
            generation=1,
            ttl_seconds=86400,
        )
        c = pub_client
        c.cookies.set(GRANT_COOKIE_NAME, token.decode("ascii"))
        data = (media_root_dir / ".agents/shared/planning/feat/design/mockups/clip.mp4").read_bytes()
        resp = c.head("/views/public/planning/ens/feat/design/mockups/clip.mp4")
        assert resp.status_code == 200
        assert resp.headers["content-length"] == str(len(data))
        assert resp.headers.get("accept-ranges") == "bytes"
        assert resp.content == b""

    def test_public_head_media_csp_matches_public_get(
        self, tmp_path, pub_cipher, pub_store, pub_passcode_service
    ):
        """All FOUR paths on the ``reports`` root carry the same CSP.

        ``reports`` is the only root the raw-lane sandbox CSP applies
        to (``_raw_lane_csp_headers``), so a mismatch is observable:
        the public HEAD media branch used to build its header dict
        from ``_HARDENING_HEADERS`` alone and OMITTED the CSP, while
        public GET / direct GET / direct HEAD all included it —
        ``HEAD /views/public/reports/x.mp4`` answered
        ``Content-Security-Policy: None``.

        Mutation that flips this RED: drop
        ``**_raw_lane_csp_headers(root_name)`` from ``_public_head``'s
        granted-media branch — the public-HEAD value becomes ``None``.
        """
        media_dir = tmp_path / "reports-root"
        media_dir.mkdir()
        (media_dir / "clip.mp4").write_bytes(b"\x00" * 64)

        rel = "clip.mp4"
        pub_store.upsert(
            "reports", rel, wrapped_passcode=pub_cipher.wrap(b"secret"), generation=1
        )
        cfg = LiveViewsConfig()
        cfg.roots["reports"] = LiveViewsRootConfig(
            type="filesystem",
            path=str(media_dir),
            allowed_extensions=["mp4"],
        )
        service = LiveViewsService(config=cfg)
        app = FastAPI()
        app.include_router(build_router())
        app.state.live_views_service = service
        app.state.view_passcode_service = pub_passcode_service
        app.state.view_passcode_cipher = pub_cipher
        app.state.view_passcode_grant_ttl_seconds = 86400
        app.state.verify_rate_limiter = VerifyRateLimiter(
            capacity=50, refill_per_minute=50
        )
        c = TestClient(app)
        token = issue_grant_cookie(
            cipher=pub_cipher,
            root_name="reports",
            rel_path=rel,
            generation=1,
            ttl_seconds=86400,
        )
        c.cookies.set(GRANT_COOKIE_NAME, token.decode("ascii"))

        expected = "sandbox allow-scripts"
        public_get = c.get(f"/views/public/reports/{rel}")
        public_head = c.head(f"/views/public/reports/{rel}")
        direct_get = c.get(f"/views/reports/{rel}")
        direct_head = c.head(f"/views/reports/{rel}")

        for label, resp in (
            ("public GET", public_get),
            ("public HEAD", public_head),
            ("direct GET", direct_get),
            ("direct HEAD", direct_head),
        ):
            assert resp.status_code == 200, label
            assert (
                resp.headers.get("content-security-policy") == expected
            ), f"{label}: {resp.headers.get('content-security-policy')!r}"


# ===========================================================================
# Group 5 — Over-media-cap
# ===========================================================================


class TestOverMediaCap:
    def test_413_named_body(self, tmp_path):
        workdir = tmp_path / "project"
        mockup = workdir / ".agents" / "shared" / "planning" / "feat" / "design" / "mockups"
        mockup.mkdir(parents=True)
        (mockup / "big.mp4").write_bytes(b"x" * 2048)
        c = _client_with_workdir(workdir, max_media_bytes=1024)
        resp = c.get(_url("big.mp4"))
        assert resp.status_code == 413
        assert resp.json() == {
            "error": "file too large",
            "reason": "exceeds media serve cap",
            "size_bytes": 2048,
            "max_bytes": 1024,
        }


# ===========================================================================
# Group 6 — Non-media over 32 MiB
# ===========================================================================


class TestNonMediaOverCap:
    def test_404_uniform(self, media_root_dir):
        mockup = media_root_dir / ".agents" / "shared" / "planning" / "feat" / "design" / "mockups"
        big = mockup / "big.html"
        with open(big, "wb") as f:
            f.truncate(MAX_SERVED_BYTES + 1)
        c = _client_with_workdir(media_root_dir)
        resp = c.get(_url("big.html"))
        assert resp.status_code == 404
        assert resp.json() == {"error": "view not found"}


# ===========================================================================
# Group 7 — Traversal blocked for media
# ===========================================================================


class TestTraversalMedia:
    def test_dotdot(self, media_root_dir):
        c = _client_with_workdir(media_root_dir)
        resp = c.get("/views/designer-artifact/ens/feat/design/mockups/../../../etc/passwd.mp4")
        assert resp.status_code == 404
        assert resp.json() == {"error": "view not found"}

    def test_absolute(self, media_root_dir):
        c = _client_with_workdir(media_root_dir)
        resp = c.get("/views/designer-artifact/ens//etc/passwd.mp4")
        assert resp.status_code == 404

    @pytest.mark.skipif(os.name == "nt", reason="POSIX symlink only")
    def test_symlink_escape(self, media_root_dir, tmp_path):
        mockup = media_root_dir / ".agents" / "shared" / "planning" / "feat" / "design" / "mockups"
        outside = tmp_path / "outside.mp4"
        outside.write_bytes(b"SECRET" * 10)
        link = mockup / "escape.mp4"
        try:
            link.symlink_to(outside)
        except OSError:
            pytest.skip("symlink not supported")
        c = _client_with_workdir(media_root_dir)
        resp = c.get(_url("escape.mp4"))
        assert resp.status_code == 404
        assert resp.json() == {"error": "view not found"}


# ===========================================================================
# Group 8 — Public lane
# ===========================================================================


class TestPublicLaneMedia:
    def test_no_row_404(self, pub_client):
        # media file exists on disk but no passcode row
        resp = pub_client.get("/views/public/planning/ens/feat/design/mockups/clip.mp4")
        assert resp.status_code == 404
        # negative control: not 200
        assert resp.status_code != 200

    def test_granted_200_byte_identical_to_direct(self, pub_client, pub_store, pub_cipher, media_root_dir):
        wrapped = pub_cipher.wrap(b"secret")
        pub_store.upsert("planning", "ens/feat/design/mockups/clip.mp4", wrapped_passcode=wrapped)
        token = issue_grant_cookie(
            cipher=pub_cipher,
            root_name="planning",
            rel_path="ens/feat/design/mockups/clip.mp4",
            generation=1,
            ttl_seconds=86400,
        )
        pub_client.cookies.set(GRANT_COOKIE_NAME, token.decode("ascii"))
        direct = _client_with_workdir(media_root_dir)
        dresp = direct.get(_url("clip.mp4"))
        presp = pub_client.get("/views/public/planning/ens/feat/design/mockups/clip.mp4")
        assert presp.status_code == 200
        assert presp.content == dresp.content
        assert presp.headers["content-type"] == dresp.headers["content-type"]

    def test_granted_206_byte_identical(self, pub_client, pub_store, pub_cipher, media_root_dir):
        wrapped = pub_cipher.wrap(b"secret")
        pub_store.upsert("planning", "ens/feat/design/mockups/clip.mp4", wrapped_passcode=wrapped)
        token = issue_grant_cookie(
            cipher=pub_cipher,
            root_name="planning",
            rel_path="ens/feat/design/mockups/clip.mp4",
            generation=1,
            ttl_seconds=86400,
        )
        pub_client.cookies.set(GRANT_COOKIE_NAME, token.decode("ascii"))
        direct = _client_with_workdir(media_root_dir)
        dresp = direct.get(_url("clip.mp4"), headers={"Range": "bytes=0-99"})
        presp = pub_client.get("/views/public/planning/ens/feat/design/mockups/clip.mp4", headers={"Range": "bytes=0-99"})
        assert presp.status_code == 206
        assert presp.content == dresp.content
        assert presp.headers.get("content-range") == dresp.headers.get("content-range")

    def test_locked_page(self, pub_client, pub_store, pub_cipher):
        wrapped = pub_cipher.wrap(b"secret")
        pub_store.upsert("planning", "ens/feat/design/mockups/clip.mp4", wrapped_passcode=wrapped, locked=True)
        resp = pub_client.get("/views/public/planning/ens/feat/design/mockups/clip.mp4")
        assert resp.status_code == 200
        assert resp.content != b""  # locked page renders a body


# ===========================================================================
# Group 9 — Non-media no-regression
# ===========================================================================


class TestNonMediaNoRegression:
    def test_html_no_accept_ranges(self, media_root_dir):
        c = _client_with_workdir(media_root_dir)
        data = (media_root_dir / ".agents/shared/planning/feat/design/mockups/landing.html").read_bytes()
        resp = c.get(_url("landing.html"))
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "text/html; charset=utf-8"
        assert "accept-ranges" not in resp.headers
        assert "content-range" not in resp.headers
        assert resp.content == data

    def test_png_no_accept_ranges(self, media_root_dir):
        c = _client_with_workdir(media_root_dir)
        data = (media_root_dir / ".agents/shared/planning/feat/design/mockups/image.png").read_bytes()
        resp = c.get(_url("image.png"))
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/png"
        assert "accept-ranges" not in resp.headers
        assert "content-range" not in resp.headers
        assert resp.content == data


# ===========================================================================
# Group 10 — Negative controls
# ===========================================================================


class TestNegativeControls:
    def test_200_full_has_no_content_range(self, media_root_dir):
        # (c) Content-Range ABSENT on the 200 full-file response.
        c = _client_with_workdir(media_root_dir)
        resp = c.get(_url("clip.mp4"))
        assert resp.status_code == 200
        assert "content-range" not in resp.headers

    def test_mid_chunk_body_less_than_full(self, media_root_dir):
        # (a) mid-chunk 206 body length < full-file length.
        c = _client_with_workdir(media_root_dir)
        data = (media_root_dir / ".agents/shared/planning/feat/design/mockups/clip.mp4").read_bytes()
        resp = c.get(_url("clip.mp4"), headers={"Range": "bytes=0-99"})
        assert resp.status_code == 206
        assert len(resp.content) < len(data)

    def test_no_row_returns_404_not_200(self, pub_client):
        # (b) gate-enforced: media via public lane WITHOUT a row
        # → 404 where a gateless implementation returns 200.
        resp = pub_client.get("/views/public/planning/ens/feat/design/mockups/clip.mp4")
        assert resp.status_code == 404


# ===========================================================================
# If-Range / ETag / Cache-Control pins
# ===========================================================================


class TestIfRangeAndCaching:
    def test_etag_strong_quoted(self, media_root_dir):
        c = _client_with_workdir(media_root_dir)
        resp = c.get(_url("clip.mp4"))
        etag = resp.headers.get("etag")
        assert etag is not None
        assert etag.startswith('"') and etag.endswith('"')

    def test_cache_control(self, media_root_dir):
        c = _client_with_workdir(media_root_dir)
        resp = c.get(_url("clip.mp4"))
        assert resp.headers.get("cache-control") == "private, max-age=60"

    def test_if_range_matching_etag_206(self, media_root_dir):
        c = _client_with_workdir(media_root_dir)
        first = c.get(_url("clip.mp4"))
        etag = first.headers["etag"]
        resp = c.get(_url("clip.mp4"), headers={"Range": "bytes=0-99", "If-Range": etag})
        assert resp.status_code == 206

    def test_if_range_stale_etag_200(self, media_root_dir):
        c = _client_with_workdir(media_root_dir)
        resp = c.get(_url("clip.mp4"), headers={"Range": "bytes=0-99", "If-Range": '"stale-etag"'})
        assert resp.status_code == 200
