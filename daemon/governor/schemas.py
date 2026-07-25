# Contract 5: AgentMetadata extensions for governor
from pydantic import Field
from pydantic_settings import SettingsConfigDict


class GovernorMetadataMixin:
    """Mixin to add governor-specific fields to AgentMetadata."""
    # No actual code here — just the contract spec
    # The actual field additions live in daemon/registry.py
    pass


# Spec: Add to AgentMetadata in daemon/registry.py:
# inject_allowed_models: bool = False
#
# Spec: Add to AgentRegistry.discover() in daemon/registry.py:
# inject_allowed_models=meta.get("inject_allowed_models", False)