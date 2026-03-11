"""Repository layer for database abstraction.

This module provides a clean separation between business logic and data access,
supporting multiple database backends (SQLite, PostgreSQL) through a common interface.

Architecture:
- Protocol: Abstract interface defining repository contracts
- Unit of Work: Transaction management with context managers
- Repository: Concrete implementations for different databases
- Factory: Configuration-driven repository creation
"""

from .protocol import ProjectRepositoryProtocol
from .unit_of_work import UnitOfWork, SQLiteUnitOfWork, PostgreSQLUnitOfWork
from .project_repository import SQLModelProjectRepository
from .factory import create_project_repository, DatabaseConfig

__all__ = [
    # Protocol
    "ProjectRepositoryProtocol",
    # Unit of Work
    "UnitOfWork",
    "SQLiteUnitOfWork", 
    "PostgreSQLUnitOfWork",
    # Repository
    "SQLModelProjectRepository",
    # Factory
    "create_project_repository",
    "DatabaseConfig",
]
