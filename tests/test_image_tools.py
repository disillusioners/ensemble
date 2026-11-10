"""Tests for ``daemon.tools.image_tools`` and the image-reader agent wiring.

Five coverage lanes:

  1. **Tool Import & Registration** — ``create_image_tools`` imports cleanly,
     the ``"image"`` category is mapped in ``CATEGORY_MODULES``, and the
     factory returns a non-empty list containing ``explain_image``.
  2. **Agent Definition Validation** — ``agents/image-reader/meta.json`` is
     valid JSON with the correct security posture (no bash / filesystem
     tools, empty ``team_members``), and the agent markdown files exist
     without referencing shell fetcher primitives like ``curl``, ``mktemp``,
     or ``rm``.
  3. **Agent meta.json Updates** — the 11 agents that gain the ``image``
     tool whitelist entry (``ari``, ``worker``, ``leader``, ``planner``,
     ``developer``, ``reviewer``, ``tidier``, ``approver``, ``tester``,
     ``giter``, ``devops``) all carry ``"image"`` in ``tools.allow``, and
     their soul/rule/workflow markdowns were not modified.
  4. **Security** — mocks the SSRF guard, the local-path reader, the
     memory cap, the magic-byte validator, and the content-type /
     extension cross-check so each branch is exercised without touching
     real network or real sensitive files. Uses ``unittest.mock``
     exclusively. Local paths are deliberately UNCONFINED (operator
     policy 2026-11-10): a readable path outside the project's own
     directories is read, while the SSRF / 10 MB / MIME / symlink-
     resolution floors stay exactly as shipped.
  5. **invoke_agent_and_wait Backward Compatibility** — confirms the
     ``images`` parameter is optional and defaults to ``None``, so all
     existing call sites keep working.

Pattern reference: ``tests/test_chart_tools.py`` for the
``create_image_tools``-factory + ``invoke_agent_and_wait`` patching style,
extended with mock-heavy security scenarios on the internal helpers.
"""

from __future__ import annotations

import inspect
import json
import socket
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
IMAGE_READER_DIR = REPO_ROOT / "agents" / "image-reader"
AGENTS_WITH_IMAGE_TOOL = [
    "ari",
    "worker",
    "leader",
    "planner",
    "developer",
    "reviewer",
    "tidier",
    "approver",
    "tester",
    "giter",
    "devops",
]


def _make_manager() -> MagicMock:
    """Build a mock InstanceManager wired for ``explain_image`` invocation.

    ``create_image_tools`` auto-injects ``project_id`` and the project's
    ``main_directory`` (the RELATIVE-path anchor) by calling
    ``manager._instance_repository.get(...)`` and
    ``manager._project_repository.get(...)``. Returning ``None`` from both
    repository ``.get`` calls keeps the auto-injection path deterministic:
    no project, no anchor — absolute paths still load, relative ones fall
    back to the process working directory.
    """
    manager = MagicMock()
    manager._instance_repository = MagicMock()
    manager._instance_repository.get = MagicMock(return_value=None)
    manager._project_repository = MagicMock()
    manager._project_repository.get = MagicMock(return_value=None)
    return manager


def _fake_getaddrinfo(ip: str):
    """Build a fake ``socket.getaddrinfo`` return tuple for ``ip``."""

    return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", (ip, 0))]


# ---------------------------------------------------------------------------
# Section 1 — Tool Import & Registration
# ---------------------------------------------------------------------------


class TestToolImportAndRegistration:
    """Factory + registry wiring for ``daemon.tools.image_tools``."""

    def test_image_tools_module_imports_successfully(self):
        """``from daemon.tools.image_tools import create_image_tools`` works."""
        from daemon.tools import image_tools  # noqa: F401

        assert hasattr(image_tools, "create_image_tools")
        assert callable(image_tools.create_image_tools)

    def test_image_category_registered_in_registry(self):
        """``"image"`` is mapped in ``CATEGORY_MODULES`` to the module path."""
        from daemon.tools._tool_registry import CATEGORY_MODULES

        assert "image" in CATEGORY_MODULES
        assert CATEGORY_MODULES["image"] == "daemon.tools.image_tools"

    def test_create_image_tools_returns_non_empty_list(self):
        """``create_image_tools`` produces a non-empty list of tools."""
        from daemon.tools.image_tools import create_image_tools

        tools = create_image_tools(_make_manager(), "test-instance-id")

        assert isinstance(tools, list)
        assert len(tools) >= 1

    def test_returned_tool_is_explain_image(self):
        """The first tool returned is ``explain_image`` (the documented entry)."""
        from daemon.tools.image_tools import create_image_tools

        tools = create_image_tools(_make_manager(), "test-instance-id")

        assert tools[0].name == "explain_image"

    def test_explain_image_is_registered_under_image_category(self):
        """``explain_image`` carries the ``_tool_category == "image"`` tag.

        Set by ``@register_tool_category("image")`` in ``image_tools.py``.
        Drives the ``is_rag_enabled`` / category-based filter wiring in
        ``create_instance_tools``.
        """
        from daemon.tools.image_tools import create_image_tools

        tools = create_image_tools(_make_manager(), "test-instance-id")

        assert getattr(tools[0], "_tool_category", None) == "image"

    def test_explain_image_not_registered_under_instance_category(self):
        """SECURITY: the image tool must NOT be tagged as ``"instance"``.

        Companion to ``test_chart_tools`` — if an image tool were
        mistakenly categorized as ``instance``, agents would inherit
        instance-management primitives (terminate, pause, etc.).
        """
        from daemon.tools.image_tools import create_image_tools

        tools = create_image_tools(_make_manager(), "test-instance-id")

        assert getattr(tools[0], "_tool_category", None) != "instance"

    def test_factory_creates_independent_tools_per_call(self):
        """Each factory call produces a fresh closure (no cross-instance leak)."""
        from daemon.tools.image_tools import create_image_tools

        manager = _make_manager()
        tools_a = create_image_tools(manager, "instance-a")
        tools_b = create_image_tools(manager, "instance-b")

        assert tools_a[0] is not tools_b[0]
        assert tools_a[0].name == tools_b[0].name

    def test_explain_image_carries_full_doc_attribute(self):
        """Full documentation is attached via ``_full_doc_`` for ``tool_help()``."""
        from daemon.tools.image_tools import create_image_tools

        tools = create_image_tools(_make_manager(), "test-instance-id")

        full_doc = getattr(tools[0], "_full_doc_", "")
        assert isinstance(full_doc, str)
        assert "Analyze an image" in full_doc
        assert "image-reader" in full_doc


# ---------------------------------------------------------------------------
# Section 2 — Agent Definition Validation (image-reader)
# ---------------------------------------------------------------------------


class TestImageReaderAgentDefinition:
    """Validate ``agents/image-reader/`` meta + markdown files."""

    def test_meta_json_is_valid_json(self):
        """``agents/image-reader/meta.json`` parses without errors."""
        with open(IMAGE_READER_DIR / "meta.json", "r", encoding="utf-8") as fh:
            data = json.load(fh)
        assert isinstance(data, dict)

    def test_meta_json_required_fields(self):
        """``id`` is ``image-reader`` and ``llm_model`` is ``quick``."""
        with open(IMAGE_READER_DIR / "meta.json", "r", encoding="utf-8") as fh:
            data = json.load(fh)

        assert data["id"] == "image-reader"
        assert data["llm_model"] == "quick"

    def test_meta_json_security_no_bash_in_tools(self):
        """SECURITY: ``tools.allow`` must NOT include ``"bash"``."""
        with open(IMAGE_READER_DIR / "meta.json", "r", encoding="utf-8") as fh:
            data = json.load(fh)

        allow = set(data.get("tools", {}).get("allow", []))
        assert "bash" not in allow

    def test_meta_json_security_no_filesystem_in_tools(self):
        """SECURITY: ``tools.allow`` must NOT include ``"filesystem"``.

        The image-reader agent operates on multimodal vision content
        attached to the message — it must never open local files or
        stage/download images via shell.
        """
        with open(IMAGE_READER_DIR / "meta.json", "r", encoding="utf-8") as fh:
            data = json.load(fh)

        allow = set(data.get("tools", {}).get("allow", []))
        assert "filesystem" not in allow

    def test_meta_json_team_members_is_empty(self):
        """``team_members`` is an empty list — headless service agent."""
        with open(IMAGE_READER_DIR / "meta.json", "r", encoding="utf-8") as fh:
            data = json.load(fh)

        assert data["team_members"] == []

    @pytest.mark.parametrize(
        "filename",
        ["soul.md", "rule.md", "workflow.md"],
    )
    def test_markdown_files_exist(self, filename):
        """``soul.md`` / ``rule.md`` / ``workflow.md`` exist and are readable."""
        path = IMAGE_READER_DIR / filename
        assert path.is_file(), f"{path} does not exist"

        content = path.read_text(encoding="utf-8")
        assert isinstance(content, str)
        assert len(content) > 0

    @pytest.mark.parametrize(
        "filename",
        ["soul.md", "rule.md", "workflow.md"],
    )
    def test_markdown_files_no_positive_shell_invocations(self, filename):
        """SECURITY: the docs must not ENCOURAGE shell fetcher use.

        The image-reader agent operates on multimodal vision content and
        must never fetch / stage / clean up files via shell. The docs may
        *prohibit* shell primitives (``Never use …``) but must not
        require the agent to invoke them as part of its workflow.
        """
        import re

        content = (IMAGE_READER_DIR / filename).read_text(encoding="utf-8")
        lowered = content.lower()

        # Look for explicit positive invocations like ``curl <url>``,
        # ``wget --quiet …``, ``mktemp -t …``, ``rm <file>``. A bare list
        # of forbidden tools in a parenthetical (the current pattern in
        # workflow.md's "Never use curl, wget, mktemp") does NOT match.
        positive_use = re.compile(
            r"\b(?:curl|wget|mktemp|rm)\s+(?:-{1,2}[a-z]+\s+)*[\S]"
        )
        offenders = positive_use.findall(lowered)
        assert not offenders, (
            f"{filename} contains positive shell invocations: {offenders}"
        )

    @pytest.mark.parametrize(
        "filename",
        ["rule.md", "workflow.md"],
    )
    def test_constraint_docs_prohibit_shell_fetchers(self, filename):
        """Constraint docs (``rule.md``, ``workflow.md``) must FORBID shell tools.

        Companion to ``test_markdown_files_no_positive_shell_invocations``:
        the prohibition is positive evidence that the security stance
        was preserved across refactors. ``soul.md`` is identity-focused
        and is exempt from this assertion.
        """
        content = (IMAGE_READER_DIR / filename).read_text(encoding="utf-8")
        lowered = content.lower()

        assert "never use" in lowered or "do not use" in lowered or "must not" in lowered, (
            f"{filename} lacks an explicit prohibition on shell-fetch primitives"
        )


# ---------------------------------------------------------------------------
# Section 3 — Agent meta.json Updates (11 agents gain ``"image"``)
# ---------------------------------------------------------------------------


class TestAgentMetaJsonUpdates:
    """Verify the ``image`` tool whitelist was added to all 11 agents."""

    @pytest.mark.parametrize("agent_id", AGENTS_WITH_IMAGE_TOOL)
    def test_image_in_tools_allow(self, agent_id):
        """The given agent's ``meta.json`` lists ``"image"`` in ``tools.allow``."""
        meta_path = REPO_ROOT / "agents" / agent_id / "meta.json"
        with open(meta_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)

        allow = set(data.get("tools", {}).get("allow", []))
        assert "image" in allow, f"{agent_id} missing 'image' in tools.allow"

    @pytest.mark.parametrize("agent_id", AGENTS_WITH_IMAGE_TOOL)
    @pytest.mark.parametrize("filename", ["soul.md", "rule.md", "workflow.md"])
    def test_agent_prompt_files_exist_and_readable(self, agent_id, filename):
        """soul.md / rule.md / workflow.md exist and are readable (no edits)."""
        path = REPO_ROOT / "agents" / agent_id / filename
        assert path.is_file(), f"{agent_id}/{filename} missing"

        content = path.read_text(encoding="utf-8")
        assert isinstance(content, str)
        assert len(content) > 0


# ---------------------------------------------------------------------------
# Section 4 — Security (SSRF / local-path admission / memory cap / magic
# bytes). There is no path-traversal fence to test: local-path admission is
# deliberately unconfined (``TestLocalPathNoConfinement``), so the lane
# names the honest guards instead.
# ---------------------------------------------------------------------------


class TestSsrfGuard:
    """Tests for ``_ensure_public_url`` — block private/loopback/etc. IPs."""

    def test_loopback_ipv4_blocked(self):
        """127.0.0.1 (loopback) is rejected."""
        from daemon.tools.image_tools import _ensure_public_url

        with patch(
            "socket.getaddrinfo",
            return_value=_fake_getaddrinfo("127.0.0.1"),
        ):
            url, err = _ensure_public_url("http://example.com/")

        assert url is None
        assert err is not None
        assert "non-public" in err.lower() or "blocked" in err.lower()

    def test_private_class_a_blocked(self):
        """10.0.0.1 (RFC1918 class A private) is rejected."""
        from daemon.tools.image_tools import _ensure_public_url

        with patch(
            "socket.getaddrinfo",
            return_value=_fake_getaddrinfo("10.0.0.1"),
        ):
            url, err = _ensure_public_url("http://example.com/")

        assert url is None
        assert err is not None

    def test_private_class_b_blocked(self):
        """172.16.0.1 (RFC1918 class B private) is rejected."""
        from daemon.tools.image_tools import _ensure_public_url

        with patch(
            "socket.getaddrinfo",
            return_value=_fake_getaddrinfo("172.16.0.1"),
        ):
            url, err = _ensure_public_url("http://example.com/")

        assert url is None
        assert err is not None

    def test_private_class_c_blocked(self):
        """192.168.1.1 (RFC1918 class C private) is rejected."""
        from daemon.tools.image_tools import _ensure_public_url

        with patch(
            "socket.getaddrinfo",
            return_value=_fake_getaddrinfo("192.168.1.1"),
        ):
            url, err = _ensure_public_url("http://example.com/")

        assert url is None
        assert err is not None

    def test_aws_metadata_endpoint_blocked(self):
        """169.254.169.254 (cloud metadata link-local) is rejected."""
        from daemon.tools.image_tools import _ensure_public_url

        with patch(
            "socket.getaddrinfo",
            return_value=_fake_getaddrinfo("169.254.169.254"),
        ):
            url, err = _ensure_public_url("http://example.com/")

        assert url is None
        assert err is not None

    def test_ipv6_loopback_blocked(self):
        """::1 (IPv6 loopback) is rejected via ``ipaddress.ip_address``."""
        from daemon.tools.image_tools import _ensure_public_url

        with patch(
            "socket.getaddrinfo",
            return_value=_fake_getaddrinfo("::1"),
        ):
            url, err = _ensure_public_url("http://example.com/")

        assert url is None
        assert err is not None

    def test_ftp_scheme_rejected(self):
        """Non-http(s) schemes are rejected before any DNS resolution."""
        from daemon.tools.image_tools import _ensure_public_url

        url, err = _ensure_public_url("ftp://example.com/image.png")

        assert url is None
        assert err is not None
        assert "scheme" in err.lower() or "unsupported" in err.lower()

    def test_file_scheme_rejected(self):
        """``file://`` is rejected — must not leak filesystem reads."""
        from daemon.tools.image_tools import _ensure_public_url

        url, err = _ensure_public_url("file:///etc/passwd")

        assert url is None
        assert err is not None
        assert "scheme" in err.lower() or "unsupported" in err.lower()

    def test_unresolvable_hostname_rejected(self):
        """``getaddrinfo`` raising ``gaierror`` is converted to a clean error."""
        from daemon.tools.image_tools import _ensure_public_url

        with patch(
            "socket.getaddrinfo",
            side_effect=socket.gaierror("Name or service not known"),
        ):
            url, err = _ensure_public_url("http://does-not-exist.invalid/")

        assert url is None
        assert err is not None
        assert "cannot resolve" in err.lower() or "hostname" in err.lower()

    def test_public_ip_passes_ssrf_guard(self):
        """A public IP passes the SSRF guard (negative control)."""
        from daemon.tools.image_tools import _ensure_public_url

        url = "https://example.com/image.png"
        with patch(
            "socket.getaddrinfo",
            return_value=_fake_getaddrinfo("93.184.216.34"),
        ):
            ok_url, err = _ensure_public_url(url)

        assert err is None
        assert ok_url == url

    def test_scheme_must_be_http_or_https(self):
        """``javascript:`` / ``data:`` / arbitrary schemes are rejected."""
        from daemon.tools.image_tools import _ensure_public_url

        for bad in ("javascript:alert(1)", "data:image/png;base64,abc", "gopher://x"):
            url, err = _ensure_public_url(bad)
            assert url is None
            assert err is not None


class TestLocalPathNoConfinement:
    """``_validate_local_path`` reads ANY readable path — no confinement.

    Operator policy (2026-11-10): the local-path directory confinement was
    REMOVED from ``explain_image`` / ``compare_images``. The helper still
    settles the path (existence, symlink resolution, regular-file shape)
    and the caller still applies the size cap + MIME validation, but a path
    outside the project's own directories is READ, not refused.
    """

    def test_path_outside_base_dir_accepted(self, tmp_path):
        """An absolute path outside the base dir is accepted, not refused.

        The base dir anchors RELATIVE paths only; it is never a boundary.
        """
        from daemon.tools.image_tools import _validate_local_path

        outside = tmp_path / "elsewhere" / "evil.png"
        outside.parent.mkdir()
        outside.write_bytes(_png_bytes())

        base = tmp_path / "base"
        base.mkdir()

        resolved, err = _validate_local_path(str(outside), str(base))

        assert err is None, err
        assert resolved == outside.resolve()

    def test_path_with_no_base_dir_accepted(self, tmp_path):
        """A resolvable absolute path is accepted with no base dir at all."""
        from daemon.tools.image_tools import _validate_local_path

        image = tmp_path / "shot.png"
        image.write_bytes(_png_bytes())

        for base_dir in (None, "", "   "):
            resolved, err = _validate_local_path(str(image), base_dir)
            assert err is None, (base_dir, err)
            assert resolved == image.resolve()

    def test_nonexistent_path_is_an_honest_error(self, tmp_path):
        """A path that does not exist yields ``file does not exist``."""
        from daemon.tools.image_tools import _validate_local_path

        base = tmp_path / "base"
        base.mkdir()

        resolved, err = _validate_local_path(
            str(base / "does-not-exist.png"), str(base)
        )

        assert resolved is None
        assert err is not None
        assert "does not exist" in err.lower()
        # An honest load error, NOT a scope refusal.
        assert "outside" not in err.lower()
        assert "allowed director" not in err.lower()

    def test_symlink_to_readable_file_is_accepted(self, tmp_path):
        """A symlink pointing outside the base dir resolves and is read.

        ``Path.resolve(strict=True)`` follows the link, so the link's target
        is what gets validated — a link to a readable file is exactly as
        good as the file itself.
        """
        from daemon.tools.image_tools import _validate_local_path

        base = tmp_path / "base"
        base.mkdir()
        outside = tmp_path / "outside.png"
        outside.write_bytes(_png_bytes())

        symlink = base / "sneaky.png"
        symlink.symlink_to(outside)

        resolved, err = _validate_local_path(str(symlink), str(base))

        assert err is None, err
        assert resolved == outside.resolve()

    def test_directory_rejected(self, tmp_path):
        """A directory path is rejected (not a regular file)."""
        from daemon.tools.image_tools import _validate_local_path

        base = tmp_path / "base"
        base.mkdir()
        subdir = base / "subdir"
        subdir.mkdir()

        resolved, err = _validate_local_path(str(subdir), str(base))

        assert resolved is None
        assert err is not None
        assert "regular file" in err.lower()

    def test_relative_path_anchors_at_base_dir(self, tmp_path):
        """A relative path resolves against the base dir (anchor, not fence)."""
        from daemon.tools.image_tools import _validate_local_path

        base = tmp_path / "base"
        base.mkdir()
        inside = base / "good.png"
        inside.write_bytes(_png_bytes())

        resolved, err = _validate_local_path("good.png", str(base))

        assert err is None
        assert resolved is not None
        assert resolved.resolve() == inside.resolve()

    def test_relative_traversal_out_of_base_dir_accepted(self, tmp_path):
        """``..`` out of the base dir resolves and is accepted."""
        from daemon.tools.image_tools import _validate_local_path

        base = tmp_path / "base"
        base.mkdir()
        outside = tmp_path / "secret.png"
        outside.write_bytes(_png_bytes())

        resolved, err = _validate_local_path("../secret.png", str(base))

        assert err is None, err
        assert resolved == outside.resolve()

    def test_embedded_nul_byte_is_an_honest_error(self, tmp_path):
        """A NUL byte in the path → the honest ``(None, msg)`` envelope.

        ``Path`` accepts the string but ``resolve(strict=True)`` raises a
        raw ``ValueError`` ("embedded null character") that used to escape
        the validator's error envelope. Weird input must yield the same
        honest error as any other unresolvable path — never a raise.
        """
        from daemon.tools.image_tools import _validate_local_path

        base = tmp_path / "base"
        base.mkdir()
        nul_path = str(base / "evil\x00.png")

        resolved, err = _validate_local_path(nul_path, str(base))

        assert resolved is None
        assert err is not None
        assert "cannot resolve" in err.lower()
        # The message quotes the raw path (``{path_str!r}``).
        assert repr(nul_path) in err


class TestMemoryCap:
    """Tests for the 10MB size cap (URL and local path branches)."""

    def test_local_file_oversize_rejected(self, tmp_path):
        """A local file whose ``stat().st_size`` exceeds 10MB is rejected."""
        from daemon.tools.image_tools import _MAX_IMAGE_BYTES, _load_image_from_path

        workdir = tmp_path / "workdir"
        workdir.mkdir()
        big = workdir / "big.png"
        # Create the file empty; we'll mock ``stat`` to claim it's huge
        # to avoid actually writing 10MB to disk.
        big.write_bytes(_png_bytes())

        real_stat = big.stat()

        class FakeStat:
            st_mode = real_stat.st_mode
            st_size = _MAX_IMAGE_BYTES + 1  # one byte over the cap

        with patch.object(Path, "stat", return_value=FakeStat()):
            with pytest.raises(ValueError) as exc_info:
                _load_image_from_path(str(big), base_dir=str(workdir))

        assert "exceeds maximum size" in str(exc_info.value).lower()
        assert str(_MAX_IMAGE_BYTES) in str(exc_info.value)

    def test_local_file_undersize_accepted(self, tmp_path):
        """A small valid PNG inside the workdir passes all guards."""
        from daemon.tools.image_tools import _load_image_from_path

        workdir = tmp_path / "workdir"
        workdir.mkdir()
        valid_png = (
            b"\x89PNG\r\n\x1a\n"  # signature
            b"xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"  # 32 bytes payload
        )
        img = workdir / "ok.png"
        img.write_bytes(valid_png)

        data_uri = _load_image_from_path(str(img), base_dir=str(workdir))

        assert data_uri.startswith("data:image/png;base64,")
        # The base64 chunk is non-empty
        assert len(data_uri) > len("data:image/png;base64,")


class TestMagicByteValidation:
    """Tests for the magic-byte-first format detection + cross-check."""

    def test_text_file_with_png_extension_rejected(self, tmp_path):
        """A text file with ``.png`` extension is rejected (magic bytes first)."""
        from daemon.tools.image_tools import _load_image_from_path

        workdir = tmp_path / "workdir"
        workdir.mkdir()
        fake = workdir / "fake.png"
        fake.write_text("This is not actually a PNG.", encoding="utf-8")

        with pytest.raises(ValueError) as exc_info:
            _load_image_from_path(str(fake), base_dir=str(workdir))

        assert "magic bytes" in str(exc_info.value).lower() or (
            "format" in str(exc_info.value).lower()
        )

    def test_format_mismatch_rejected(self, tmp_path):
        """A ``.png``-named file whose magic bytes are JPEG is rejected.

        The detection layer detects JPEG (magic bytes win), and the
        extension hint claims PNG → mismatch raises.
        """
        from daemon.tools.image_tools import _load_image_from_path

        workdir = tmp_path / "workdir"
        workdir.mkdir()
        bad = workdir / "lying.png"
        # JPEG signature at offset 0
        bad.write_bytes(b"\xff\xd8\xff" + b"x" * 100)

        with pytest.raises(ValueError) as exc_info:
            _load_image_from_path(str(bad), base_dir=str(workdir))

        msg = str(exc_info.value).lower()
        assert "format mismatch" in msg or "mismatch" in msg

    def test_real_png_accepted(self, tmp_path):
        """A real PNG (matching magic bytes and extension) loads cleanly."""
        from daemon.tools.image_tools import _load_image_from_path

        workdir = tmp_path / "workdir"
        workdir.mkdir()
        good = workdir / "good.png"
        good.write_bytes(
            b"\x89PNG\r\n\x1a\n" + b"x" * 32,
        )

        data_uri = _load_image_from_path(str(good), base_dir=str(workdir))

        assert data_uri.startswith("data:image/png;base64,")

    def test_real_jpeg_accepted(self, tmp_path):
        """A real JPEG loads cleanly."""
        from daemon.tools.image_tools import _load_image_from_path

        workdir = tmp_path / "workdir"
        workdir.mkdir()
        good = workdir / "good.jpg"
        good.write_bytes(b"\xff\xd8\xff" + b"x" * 32)

        data_uri = _load_image_from_path(str(good), base_dir=str(workdir))

        assert data_uri.startswith("data:image/jpeg;base64,")

    def test_real_webp_accepted(self, tmp_path):
        """A real WebP (RIFF....WEBP) loads cleanly."""
        from daemon.tools.image_tools import _load_image_from_path

        workdir = tmp_path / "workdir"
        workdir.mkdir()
        good = workdir / "good.webp"
        # RIFF at offset 0, WEBP at offset 8
        good.write_bytes(b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"x" * 16)

        data_uri = _load_image_from_path(str(good), base_dir=str(workdir))

        assert data_uri.startswith("data:image/webp;base64,")


class TestLoadImageAsDataUriDispatch:
    """Dispatch between URL and path branches in ``_load_image_as_data_uri``."""

    async def test_empty_input_rejected(self):
        """An empty ``image`` string is rejected."""
        from daemon.tools.image_tools import _load_image_as_data_uri

        with pytest.raises(ValueError) as exc_info:
            await _load_image_as_data_uri("")

        assert "empty" in str(exc_info.value).lower() or (
            "non-empty" in str(exc_info.value).lower()
        )

    async def test_url_with_private_ip_rejected(self):
        """A URL whose hostname resolves to a private IP is rejected."""
        from daemon.tools.image_tools import _load_image_as_data_uri

        with patch(
            "socket.getaddrinfo",
            return_value=_fake_getaddrinfo("127.0.0.1"),
        ):
            with pytest.raises(ValueError) as exc_info:
                await _load_image_as_data_uri("http://example.com/image.png")

        assert "non-public" in str(exc_info.value).lower() or (
            "blocked" in str(exc_info.value).lower()
        )

    async def test_whitespace_only_input_rejected(self):
        """Whitespace-only input is rejected after strip()."""
        from daemon.tools.image_tools import _load_image_as_data_uri

        with pytest.raises(ValueError) as exc_info:
            await _load_image_as_data_uri("   \n\t  ")

        msg = str(exc_info.value).lower()
        assert "empty" in msg or "non-empty" in msg


# ---------------------------------------------------------------------------
# Section 5 — invoke_agent_and_wait backward compatibility
# ---------------------------------------------------------------------------


class TestInvokeAgentAndWaitBackwardCompat:
    """The ``images`` parameter must be optional and default to ``None``."""

    def test_signature_has_images_optional(self):
        """The function signature accepts ``images=None``."""
        from daemon.utils import invoke_agent_and_wait

        sig = inspect.signature(invoke_agent_and_wait)
        assert "images" in sig.parameters
        param = sig.parameters["images"]
        # ``Parameter.empty`` default would mean required; ``None`` means optional.
        assert param.default is None

    def test_signature_keeps_backward_compatible_positionals(self):
        """All other parameters keep their documented positions/defaults."""
        from daemon.utils import invoke_agent_and_wait

        sig = inspect.signature(invoke_agent_and_wait)
        params = sig.parameters

        # Mandatory-keyword shape: agent_id, message are required.
        # images, timeout, return_instance_id are optional.
        for required in ("manager", "agent_id", "message"):
            assert required in params, f"missing required param {required}"

        for optional in ("images", "timeout", "return_instance_id"):
            assert optional in params, f"missing optional param {optional}"
            assert params[optional].default is not inspect.Parameter.empty

    def test_images_default_is_none_literal(self):
        """``images=None`` is a documented default (not ``...`` or sentinel).

        The default ``None`` is what makes the parameter optional for
        backward compatibility — call sites written before the vision
        pipeline was added all omit ``images=`` and rely on this.
        """
        from daemon.utils import invoke_agent_and_wait

        sig = inspect.signature(invoke_agent_and_wait)
        assert sig.parameters["images"].default is None

    def test_signature_accepts_call_without_images_kwarg(self):
        """Synthetic call site omitting ``images=`` binds cleanly to the signature.

        Mirrors the contract used by the legacy call sites (e.g.
        ``invoke_agent_and_wait(manager=..., agent_id=..., message=...)``).
        """
        from daemon.utils import invoke_agent_and_wait

        sig = inspect.signature(invoke_agent_and_wait)
        bound = sig.bind(
            manager=MagicMock(),
            agent_id="x",
            message="m",
            timeout=1.0,
        )
        bound.apply_defaults()
        # ``images`` defaulted to None, others flowed through.
        assert bound.arguments["images"] is None
        assert bound.arguments["agent_id"] == "x"
        assert bound.arguments["message"] == "m"


# ---------------------------------------------------------------------------
# Section 6b — local paths are UNCONFINED (operator policy 2026-11-10)
# ---------------------------------------------------------------------------


def _png_bytes() -> bytes:
    """A minimal valid PNG payload (signature + filler)."""
    return b"\x89PNG\r\n\x1a\n" + b"x" * 32


def _scope_manager(main_directory, related_directories=()):
    """Build a manager whose instance resolves to a project with dirs.

    ``explain_image`` auto-injects ``project_id`` from the calling instance
    and the project's ``main_directory`` as the RELATIVE-path anchor. The
    mock mirrors that two-hop lookup exactly. ``related_directories`` is
    accepted (and ignored by the tool) so the pins can prove the declared
    scope plays no part in path admission.
    """
    manager = MagicMock()
    manager._instance_repository = MagicMock()
    manager._instance_repository.get = MagicMock(
        return_value=MagicMock(project_id="pid"),
    )
    manager._project_repository = MagicMock()
    manager._project_repository.get = MagicMock(
        return_value=MagicMock(
            main_directory=main_directory,
            related_directories=list(related_directories),
        ),
    )
    return manager


class TestExplainImageUnconfinedPath:
    """``explain_image`` reads ANY readable local path (no confinement).

    Operator policy 2026-11-10 reverses the be8ee6295 D1 confinement: a
    local path that exists and is readable is fetched, base64-encoded and
    dispatched — whether it sits under the project's directories or not.
    A nonexistent / unreadable path returns an honest load error, never a
    scope refusal.
    """

    def test_path_under_main_directory_is_accepted(self, tmp_path):
        """A file under ``main_directory`` still loads."""
        from daemon.tools.image_tools import _validate_local_path

        main = tmp_path / "frontend-engine"
        main.mkdir()
        image = main / "shot.png"
        image.write_bytes(_png_bytes())

        resolved, err = _validate_local_path(str(image), str(main))

        assert err is None, err
        assert resolved == image.resolve()

    def test_path_outside_every_project_directory_is_accepted(self, tmp_path):
        """A readable file outside every project directory is READ, not refused."""
        from daemon.tools.image_tools import _validate_local_path
        from daemon.tools.image_tools import _load_image_from_path

        main = tmp_path / "frontend-engine"
        main.mkdir()
        video = tmp_path / "video-lab"
        video.mkdir()
        outside = tmp_path / "elsewhere" / "secret.png"
        outside.parent.mkdir()
        outside.write_bytes(_png_bytes())

        resolved, err = _validate_local_path(str(outside), str(main))

        assert err is None, err
        assert resolved == outside.resolve()
        uri = _load_image_from_path(str(outside), base_dir=str(main))
        assert uri.startswith("data:image/png;base64,")

    def test_path_with_no_project_at_all_is_accepted(self, tmp_path):
        """No project / no main directory → the path still loads."""
        from daemon.tools.image_tools import _load_image_from_path

        image = tmp_path / "shot.png"
        image.write_bytes(_png_bytes())

        for base_dir in (None, "", "   "):
            uri = _load_image_from_path(str(image), base_dir=base_dir)
            assert uri.startswith("data:image/png;base64,"), base_dir

    def test_relative_path_anchors_at_the_main_directory(self, tmp_path):
        """Relative paths still resolve against ``main_directory``."""
        from daemon.tools.image_tools import _validate_local_path

        main = tmp_path / "frontend-engine"
        main.mkdir()
        image = main / "shot.png"
        image.write_bytes(_png_bytes())

        resolved, err = _validate_local_path("shot.png", str(main))

        assert err is None, err
        assert resolved == image.resolve()

    def test_symlink_to_readable_file_is_accepted(self, tmp_path):
        """A symlink outside the project dirs resolves and loads."""
        from daemon.tools.image_tools import _load_image_from_path

        main = tmp_path / "frontend-engine"
        main.mkdir()
        outside = tmp_path / "secret.png"
        outside.write_bytes(_png_bytes())
        link = main / "alias.png"
        link.symlink_to(outside)

        uri = _load_image_from_path(str(link), base_dir=str(main))

        assert uri.startswith("data:image/png;base64,")

    def test_nonexistent_path_is_an_honest_error_not_a_refusal(self, tmp_path):
        """A missing path → ``file does not exist``, never a scope refusal."""
        from daemon.tools.image_tools import _load_image_from_path

        main = tmp_path / "frontend-engine"
        main.mkdir()

        with pytest.raises(ValueError) as exc_info:
            _load_image_from_path(
                str(tmp_path / "nothing-here.png"), base_dir=str(main)
            )

        msg = str(exc_info.value).lower()
        assert "does not exist" in msg
        assert "outside" not in msg
        assert "allowed director" not in msg
        assert "related director" not in msg

    def test_safety_floor_still_applies_outside_the_project(self, tmp_path):
        """The non-image / oversize floors are unchanged for outside paths."""
        from daemon.tools.image_tools import _load_image_from_path

        main = tmp_path / "frontend-engine"
        main.mkdir()
        outside = tmp_path / "not-an-image.png"
        outside.write_text("definitely not a real image", encoding="utf-8")

        with pytest.raises(ValueError) as exc_info:
            _load_image_from_path(str(outside), base_dir=str(main))

        assert "magic bytes" in str(exc_info.value).lower() or (
            "format" in str(exc_info.value).lower()
        )

    async def test_explain_image_reads_declared_related_directory_path(
        self, tmp_path
    ):
        """END-TO-END: a path INSIDE a declared related directory dispatches.

        Reachability pin, not an outside-scope pin: ``video-lab`` is
        declared as a related directory and the contact sheet lives under
        it — this exact read path was the reachable-scope case the
        be8ee6295 D1 confinement admitted, and it must STAY reachable now
        that the confinement is gone (the unconfined policy is a superset
        of the old in-scope set). Never delete this as redundant with the
        outside-path tests — it proves the declared-scope lane survived
        the policy change.
        """
        from daemon.tools.image_tools import create_image_tools

        main = tmp_path / "frontend-engine"
        main.mkdir()
        video = tmp_path / "video-lab"
        (video / "prod-v2" / "review").mkdir(parents=True)
        image = video / "prod-v2" / "review" / "contact-sheet.png"
        image.write_bytes(_png_bytes())

        manager = _scope_manager(str(main), [str(video)])
        mock_invoke = AsyncMock(return_value=("sheet analysis", "child-id"))

        with patch("daemon.tools.image_tools.invoke_agent_and_wait", mock_invoke):
            tools = create_image_tools(manager, "parent-instance-id")
            result = await tools[0].coroutine(
                image=str(image), question="What is shown?"
            )

        assert result == "sheet analysis"
        mock_invoke.assert_awaited_once()
        assert mock_invoke.call_args.kwargs["images"][0].startswith(
            "data:image/png;base64,"
        )

    async def test_explain_image_reads_arbitrary_outside_path(self, tmp_path):
        """END-TO-END: a path in an entirely unrelated directory dispatches."""
        from daemon.tools.image_tools import create_image_tools

        main = tmp_path / "frontend-engine"
        main.mkdir()
        elsewhere = tmp_path / "some-other-place" / "deep" / "asset.png"
        elsewhere.parent.mkdir(parents=True)
        elsewhere.write_bytes(_png_bytes())

        manager = _scope_manager(str(main))
        mock_invoke = AsyncMock(return_value=("analysis", "child-id"))

        with patch("daemon.tools.image_tools.invoke_agent_and_wait", mock_invoke):
            tools = create_image_tools(manager, "parent-instance-id")
            result = await tools[0].coroutine(image=str(elsewhere), question="?")

        assert result == "analysis"
        mock_invoke.assert_awaited_once()
        assert mock_invoke.call_args.kwargs["images"][0].startswith(
            "data:image/png;base64,"
        )

    async def test_explain_image_reports_honest_error_for_missing_path(
        self, tmp_path
    ):
        """A missing path → ``Error:`` string from the tool, no dispatch."""
        from daemon.tools.image_tools import create_image_tools

        main = tmp_path / "frontend-engine"
        main.mkdir()
        missing = tmp_path / "gone" / "never.png"

        manager = _scope_manager(str(main))
        mock_invoke = AsyncMock()

        with patch("daemon.tools.image_tools.invoke_agent_and_wait", mock_invoke):
            tools = create_image_tools(manager, "parent-instance-id")
            result = await tools[0].coroutine(image=str(missing), question="?")

        assert result.startswith("Error:")
        assert "does not exist" in result.lower()
        # An honest load error, not a scope refusal naming project roots.
        assert "allowed directories" not in result.lower()
        assert "related directories" not in result.lower()
        mock_invoke.assert_not_awaited()


# ---------------------------------------------------------------------------
# Section 6c — docstring pins: no confinement / no scope-refusal language
# ---------------------------------------------------------------------------

# Phrases that describe the REMOVED directory confinement. Their presence in
# the LIVE tool documentation (description / _full_doc_) means the doc was
# re-confined without the code following, or vice versa — either way a
# caller-facing lie.
_CONFINEMENT_PHRASES = (
    "confine",
    "allowed director",
    "related director",
    "project workdir",
    "workdir boundary",
    "outside the project",
)


class TestExplainImageDocsDropConfinement:
    """``explain_image`` documents unconfined local reads (negative pin)."""

    def test_description_has_no_confinement_language(self):
        from daemon.tools.image_tools import create_image_tools

        tool = create_image_tools(_make_manager(), "iid")[0]
        # Whitespace-collapsed, matching the ``_full_doc_`` pin below and
        # the compare_tools description pin: the doc is hard-wrapped, so a
        # phrase like ``allowed\ndirectories`` must not evade by newline.
        description = " ".join((tool.description or "").lower().split())

        for phrase in _CONFINEMENT_PHRASES:
            assert phrase not in description, (
                f"explain_image.description still mentions {phrase!r} — "
                "the local-path confinement was removed; the doc must say "
                "so."
            )
        # Positive side of the same contract, in its own separate assertion.
        assert "any readable path" in description

    def test_full_doc_has_no_confinement_language(self):
        from daemon.tools.image_tools import create_image_tools

        tool = create_image_tools(_make_manager(), "iid")[0]
        # Whitespace-collapsed: the doc is hard-wrapped, so a phrase can
        # straddle a newline.
        full_doc = " ".join(
            (getattr(tool, "_full_doc_", "") or "").lower().split()
        )

        for phrase in _CONFINEMENT_PHRASES:
            assert phrase not in full_doc, (
                f"explain_image._full_doc_ still mentions {phrase!r}"
            )
        assert "any readable path" in full_doc

    def test_description_still_promises_the_safety_floor(self):
        """The remaining promises (SSRF guard, 10 MB, formats) are documented."""
        from daemon.tools.image_tools import create_image_tools

        description = (create_image_tools(_make_manager(), "iid")[0].description or "").lower()

        assert "ssrf" in description
        assert "10 mb" in description
        assert "png" in description and "webp" in description


# ---------------------------------------------------------------------------
# Section 6 — explain_image delegation (factory + ``invoke_agent_and_wait``)
# ---------------------------------------------------------------------------


class TestExplainImageDelegation:
    """``explain_image`` constructs the right ``invoke_agent_and_wait`` kwargs."""

    async def test_explain_image_uses_image_reader_agent_id(self):
        """``explain_image`` delegates to ``agent_id="image-reader"``."""
        from daemon.tools.image_tools import create_image_tools

        manager = _make_manager()
        mock_invoke = AsyncMock(return_value=("analysis text", "child-id"))

        with patch("daemon.tools.image_tools.invoke_agent_and_wait", mock_invoke):
            tools = create_image_tools(manager, "parent-instance-id")

            # Need a loaded image — pass a tiny PNG via temp file path.
            import tempfile
            import os
            fd, path = tempfile.mkstemp(suffix=".png")
            try:
                with os.fdopen(fd, "wb") as fh:
                    fh.write(b"\x89PNG\r\n\x1a\n" + b"x" * 32)
                # The tool looks up ``main_directory`` as the RELATIVE-path
                # anchor. The path below is absolute, so this anchor is
                # never exercised — it is wired anyway so the repo lookups
                # resolve deterministically instead of hitting a bare
                # MagicMock.
                manager._project_repository.get = MagicMock(
                    return_value=MagicMock(main_directory=os.path.dirname(path)),
                )
                manager._instance_repository.get = MagicMock(
                    return_value=MagicMock(project_id="pid"),
                )
                await tools[0].coroutine(image=path, question="What's this?")
            finally:
                os.unlink(path)

        mock_invoke.assert_awaited_once()
        kwargs = mock_invoke.call_args.kwargs
        assert kwargs["agent_id"] == "image-reader"
        assert kwargs["return_instance_id"] is True
        assert kwargs["timeout"] == 300.0
        assert kwargs["parent_id"] == "parent-instance-id"
        # images list carries the data URI
        assert isinstance(kwargs["images"], list)
        assert len(kwargs["images"]) == 1
        assert kwargs["images"][0].startswith("data:image/png;base64,")

    async def test_explain_image_returns_agent_response_on_success(self):
        """The agent's content string is returned verbatim on success."""
        from daemon.tools.image_tools import create_image_tools

        manager = _make_manager()
        expected = "This is a clear image of a cat."
        mock_invoke = AsyncMock(return_value=(expected, "child-id"))

        with patch("daemon.tools.image_tools.invoke_agent_and_wait", mock_invoke):
            tools = create_image_tools(manager, "parent")

            import tempfile
            import os
            fd, path = tempfile.mkstemp(suffix=".png")
            try:
                with os.fdopen(fd, "wb") as fh:
                    fh.write(b"\x89PNG\r\n\x1a\n" + b"x" * 32)
                manager._project_repository.get = MagicMock(
                    return_value=MagicMock(main_directory=os.path.dirname(path)),
                )
                manager._instance_repository.get = MagicMock(
                    return_value=MagicMock(project_id="pid"),
                )
                result = await tools[0].coroutine(image=path, question="?")
            finally:
                os.unlink(path)

        assert result == expected

    async def test_explain_image_handles_none_result(self):
        """``(None, instance_id)`` from invoke → short ``Error:`` string."""
        from daemon.tools.image_tools import create_image_tools

        manager = _make_manager()
        mock_invoke = AsyncMock(return_value=(None, "child-id"))

        with patch("daemon.tools.image_tools.invoke_agent_and_wait", mock_invoke):
            tools = create_image_tools(manager, "parent")

            import tempfile
            import os
            fd, path = tempfile.mkstemp(suffix=".png")
            try:
                with os.fdopen(fd, "wb") as fh:
                    fh.write(b"\x89PNG\r\n\x1a\n" + b"x" * 32)
                manager._project_repository.get = MagicMock(
                    return_value=MagicMock(main_directory=os.path.dirname(path)),
                )
                manager._instance_repository.get = MagicMock(
                    return_value=MagicMock(project_id="pid"),
                )
                result = await tools[0].coroutine(image=path, question="?")
            finally:
                os.unlink(path)

        assert isinstance(result, str)
        assert result.startswith("Error:")

    async def test_explain_image_returns_error_string_on_load_failure(self):
        """When image load fails (e.g. text file), tool returns ``Error:``."""
        from daemon.tools.image_tools import create_image_tools

        manager = _make_manager()
        mock_invoke = AsyncMock()  # should NOT be called

        with patch("daemon.tools.image_tools.invoke_agent_and_wait", mock_invoke):
            tools = create_image_tools(manager, "parent")

            import tempfile
            import os
            fd, path = tempfile.mkstemp(suffix=".png")
            try:
                # Write a text file with .png extension — magic-byte rejection.
                with os.fdopen(fd, "w") as fh:
                    fh.write("definitely not a real image")
                manager._project_repository.get = MagicMock(
                    return_value=MagicMock(main_directory=os.path.dirname(path)),
                )
                manager._instance_repository.get = MagicMock(
                    return_value=MagicMock(project_id="pid"),
                )
                result = await tools[0].coroutine(image=path, question="?")
            finally:
                os.unlink(path)

        assert isinstance(result, str)
        assert result.startswith("Error:")
        # Load failed → agent was never invoked.
        mock_invoke.assert_not_awaited()

    async def test_explain_image_load_failure_does_not_invoke_agent(self, tmp_path):
        """SSRF block / size cap / magic-byte mismatch → no agent dispatch.

        A missing path, an SSRF block and a format mismatch all surface as
        an ``"Error: ..."`` string from the tool itself, never raising. The
        agent is not spawned on a load failure (avoids wasting a worker
        slot).
        """
        from daemon.tools.image_tools import create_image_tools

        manager = _make_manager()
        mock_invoke = AsyncMock()
        missing = tmp_path / "definitely-missing.png"

        with patch("daemon.tools.image_tools.invoke_agent_and_wait", mock_invoke):
            tools = create_image_tools(manager, "parent")

            # A genuinely missing path → honest load error, no dispatch.
            result = await tools[0].coroutine(image=str(missing), question="?")

        assert isinstance(result, str)
        assert result.startswith("Error:")
        assert "does not exist" in result.lower()
        mock_invoke.assert_not_awaited()
