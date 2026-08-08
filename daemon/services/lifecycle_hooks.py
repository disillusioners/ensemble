"""Extensible asynchronous hooks for instance lifecycle events."""

import asyncio
import inspect
import logging
import re
from datetime import datetime, timezone
from typing import Any, Callable, NamedTuple

from daemon.services.context_tools import write_context_file

logger = logging.getLogger(__name__)
_HOOK_REGISTRY: dict[str, dict[str, Callable]] = {}


class LifecycleHookContext(NamedTuple):
    """Context supplied to lifecycle hooks.

    ``instance_id`` identifies the completed instance; ``agent_id`` identifies
    its agent; ``parent_id`` identifies its parent instance; ``last_content`` is
    the final report; ``outcome`` describes completion; ``context_key`` selects
    the shared context directory; and ``manager`` is the instance manager.
    """

    instance_id: str
    agent_id: str | None
    parent_id: str | None
    last_content: str
    outcome: str
    context_key: str | None
    manager: Any


def register_lifecycle_hook(event: str, hook_name: str, fn: Callable) -> None:
    """Register an async hook function, overwriting any same-named hook.

    ``fn`` MUST be an async callable. The registry is module-level and
    registration is idempotent.
    """
    if not inspect.iscoroutinefunction(fn):
        raise TypeError(
            f"register_lifecycle_hook: hook '{hook_name}' for event '{event}' "
            f"must be an async function (async def), got {type(fn).__name__}"
        )
    _HOOK_REGISTRY.setdefault(event, {})[hook_name] = fn


async def dispatch_lifecycle_hooks(
    event: str, hook_names: list[str], context: LifecycleHookContext
) -> None:
    """Run configured hooks in order, isolating ordinary hook failures."""
    if not hook_names:
        return
    registry = _HOOK_REGISTRY.get(event, {})
    for hook_name in hook_names:
        fn = registry.get(hook_name)
        if fn is None:
            logger.debug("hook not registered, skipping")
            continue
        try:
            await fn(context)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"hook {hook_name} failed: {e}")


def _derive_report_slug(last_content: str, instance_id: str) -> str:
    """Derive a bounded, URL-like slug from report content."""
    if not last_content:
        last_content = ""
    lines = [line.strip() for line in last_content.splitlines() if line.strip()]

    def slugify(text: str) -> str:
        return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:80]

    for line in lines:
        if line.startswith("#"):
            slug = slugify(line.lstrip("#").strip())
            if slug:
                return slug
    for line in lines:
        if (
            line.startswith(("✅", "Task Complete", "Skill(s)", "---", "```", "| ", "#"))
            or re.fullmatch(r"(?:[-*_]\s*){3,}", line)
        ):
            continue
        slug = slugify(line)
        if slug:
            return slug
    return f"child-report-{instance_id[:8]}"


async def _add_to_shared_context_md_files(ctx: LifecycleHookContext) -> None:
    """Save a completed child report into its shared context directory."""
    if ctx.context_key is None:
        logger.debug("context_key is None; hook skipped")
        return
    slug = _derive_report_slug(ctx.last_content, ctx.instance_id)
    try:
        timestamp = datetime.now(timezone.utc).isoformat()
        body = (
            f"# Child Report: {ctx.agent_id}\n\n"
            f"**Instance**: {ctx.instance_id}\n"
            f"**Time**: {timestamp}\n\n"
            f"{ctx.last_content}"
        )
        await asyncio.to_thread(
            write_context_file, ctx.context_key, body, slug, ".md", ctx.instance_id
        )
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.warning(f"hook _add_to_shared_context_md_files failed: {e}")


register_lifecycle_hook(
    "on_complete", "add_to_shared_context_md_files", _add_to_shared_context_md_files
)
