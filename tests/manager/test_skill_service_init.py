"""Tests for Skill Evolution service initialization in InstanceManager."""

import pytest

from daemon.config import Config
from daemon.manager import InstanceManager
from daemon.services.skill_embedding_service import SkillEmbeddingService
from daemon.services.skill_search_service import SkillSearchService
from daemon.services.skill_store_service import SkillStoreService


def _create_test_config(tmp_path):
    """Create a real Config with an isolated database path."""
    config = Config()
    config.persistence.db_path = str(tmp_path / "instances.db")
    config.mcp_pool.enabled = False
    return config


@pytest.mark.asyncio
async def test_skill_services_initialized_when_config_present(tmp_path):
    """Skill repositories and services are initialized when config is present."""
    config = _create_test_config(tmp_path)

    manager = InstanceManager(config)

    assert manager._skill_repo is not None
    assert manager._skill_lineage_repo is not None
    assert manager._skill_embedding_repo is not None
    assert manager._skill_embedding_service is not None
    assert manager._skill_store_service is not None
    assert manager._skill_search_service is not None
    assert isinstance(manager._skill_embedding_service, SkillEmbeddingService)
    assert isinstance(manager._skill_store_service, SkillStoreService)
    assert isinstance(manager._skill_search_service, SkillSearchService)


@pytest.mark.asyncio
async def test_skill_services_none_when_skill_evolution_disabled(tmp_path, monkeypatch):
    """Skill repositories and services are left unset when skill_evolution is None."""
    config = _create_test_config(tmp_path)
    monkeypatch.setattr(config, "skill_evolution", None)

    manager = InstanceManager(config)

    assert manager._skill_repo is None
    assert manager._skill_lineage_repo is None
    assert manager._skill_embedding_repo is None
    assert manager._skill_embedding_service is None
    assert manager._skill_store_service is None
    assert manager._skill_search_service is None


@pytest.mark.asyncio
async def test_skill_services_have_correct_dependencies(tmp_path):
    """Skill services receive the repositories/config expected by their constructors."""
    config = _create_test_config(tmp_path)

    manager = InstanceManager(config)

    assert manager._skill_embedding_service.config is manager.config.skill_evolution
    assert manager._skill_embedding_service.embedding_repo is manager._skill_embedding_repo

    expected_llm_config = {
        "base_url": manager.config.llm.base_url,
        "api_key": manager.config.llm.api_key,
        "model": manager.config.llm.model,
        "model_vision": manager.config.llm.model_vision,
        "temperature": manager.config.llm.temperature,
        "request_timeout": manager.config.llm.request_timeout,
    }
    assert manager._skill_embedding_service.llm_config == expected_llm_config

    assert manager._skill_store_service._skill_repo is manager._skill_repo
    assert manager._skill_store_service._lineage_repo is manager._skill_lineage_repo
    assert manager._skill_store_service._embedding_service is manager._skill_embedding_service

    assert manager._skill_search_service._skill_repo is manager._skill_repo
    assert manager._skill_search_service._embedding_repo is manager._skill_embedding_repo
    assert manager._skill_search_service._embedding_service is manager._skill_embedding_service
    assert manager._skill_search_service._llm_config == expected_llm_config
    assert manager._skill_search_service._config is manager.config.skill_evolution
