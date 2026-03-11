"""Project repository module.

Provides the repository implementation for Project entities with:
- Protocol: Abstract interface for database-agnostic operations
- Repository: SQLModel/SQLAlchemy implementation
- Models: Database table definitions
"""

from .protocol import ProjectData, ProjectRepositoryProtocol
from .repository import SQLModelProjectRepository
from .models import Project, ProjectTagLink, ProjectShortnameLink, ProjectStatus, ProjectType

__all__ = [
    # Protocol
    "ProjectRepositoryProtocol",
    "ProjectData",
    # Repository
    "SQLModelProjectRepository",
    # Models
    "Project",
    "ProjectTagLink",
    "ProjectShortnameLink",
    "ProjectStatus",
    "ProjectType",
]
