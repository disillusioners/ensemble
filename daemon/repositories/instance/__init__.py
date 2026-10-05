"""Instance repository module."""

from .repository import SQLModelInstanceRepository, get_agent_name
from .models import Instance, InstanceHierarchy, InstanceStatus

# Re-export CHAT_SOURCE_TYPES as a MODULE-level constant. The original
# definition lives on ``SQLModelInstanceRepository`` (so it stays adjacent
# to the source-filter methods that consume it); the module-level alias
# here is what ``from daemon.repositories.instance import CHAT_SOURCE_TYPES``
# resolves to. The dual export keeps the class attribute semantics for
# internal callers (``self.CHAT_SOURCE_TYPES``) while exposing a flat
# import for the test pack and other external consumers.
CHAT_SOURCE_TYPES = SQLModelInstanceRepository.CHAT_SOURCE_TYPES

__all__ = [
    "CHAT_SOURCE_TYPES",
    "SQLModelInstanceRepository",
    "get_agent_name",
    "Instance",
    "InstanceHierarchy",
    "InstanceStatus",
]
