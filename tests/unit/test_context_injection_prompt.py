from types import SimpleNamespace
from unittest.mock import Mock, patch

from daemon.services.instance_lifecycle import _apply_post_cache_appends


def _args(agent_meta):
    repo = Mock()
    project_repo = Mock()
    project_repo.get.return_value = SimpleNamespace(name="Project")
    project_repo.list_critical_notes.return_value = []
    return {
        "system_prompt": "base",
        "instance_id": "root-id",
        "instance_repository": repo,
        "shared_context_metadata_repo": Mock(get_all_as_dict=Mock(return_value={})),
        "parent_id": None,
        "agent_id": "leader",
        "project_id": "project-id",
        "project_repository": project_repo,
        "manager": SimpleNamespace(_skill_repo=None, _skill_clone_service=None),
        "agent_meta": agent_meta,
    }


def test_post_cache_appender_injects_context_when_enabled():
    with patch("daemon.services.instance_lifecycle.get_shared_context", return_value="Known project facts"):
        prompt, _ = _apply_post_cache_appends(**_args(SimpleNamespace(context_injection=True)))
    assert "# Injected Project Context" in prompt
    assert "Known project facts" in prompt


def test_post_cache_appender_skips_context_when_disabled():
    with patch("daemon.services.instance_lifecycle.get_shared_context") as get_context:
        prompt, _ = _apply_post_cache_appends(**_args(SimpleNamespace(context_injection=False)))
    get_context.assert_not_called()
    assert "# Injected Project Context" not in prompt


def test_post_cache_appender_handles_empty_context():
    with patch("daemon.services.instance_lifecycle.get_shared_context", return_value=""):
        prompt, _ = _apply_post_cache_appends(**_args(SimpleNamespace(context_injection=True)))
    assert "# Injected Project Context" not in prompt
    assert "base" in prompt


def test_post_cache_appender_swallows_exception():
    with patch(
        "daemon.services.instance_lifecycle.get_shared_context",
        side_effect=RuntimeError("boom"),
    ):
        prompt, _ = _apply_post_cache_appends(**_args(SimpleNamespace(context_injection=True)))
    assert "# Injected Project Context" not in prompt
    assert "base" in prompt


def test_post_cache_appender_resolves_child_context_key():
    args = _args(SimpleNamespace(context_injection=True))
    args["parent_id"] = "parent-id"
    args["instance_repository"].get_tree_root_id.return_value = "root-key"
    with patch(
        "daemon.services.instance_lifecycle.get_shared_context",
        return_value="Child facts",
    ) as get_context:
        prompt, _ = _apply_post_cache_appends(**args)
    # ``get_tree_root_id`` is called by multiple appenders in the chain,
    # so we use ``assert_any_call`` rather than ``assert_called_once_with``.
    args["instance_repository"].get_tree_root_id.assert_any_call("parent-id")
    get_context.assert_called_once()
    # The first positional arg of ``get_shared_context`` is the context_key.
    assert get_context.call_args.args[0] == "root-key"
    assert "# Injected Project Context" in prompt
    assert "Child facts" in prompt


def test_post_cache_appender_handles_none_agent_meta():
    # ``agent_meta=None`` exercises the
    # ``getattr(agent_meta, "context_injection", False)`` defensive guard
    # so the appender must not crash and must not call ``get_shared_context``.
    args = _args(None)
    with patch("daemon.services.instance_lifecycle.get_shared_context") as get_context:
        prompt, _ = _apply_post_cache_appends(**args)
    get_context.assert_not_called()
    assert "# Injected Project Context" not in prompt
