"""Unit of Work pattern for transaction management.

Provides context managers for database sessions with automatic commit/rollback,
ensuring proper transaction boundaries and resource cleanup.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from contextlib import contextmanager
from typing import Generator, Any

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine


class UnitOfWork(ABC):
    """Abstract Unit of Work for managing database transactions.
    
    Usage:
        with unit_of_work as uow:
            uow.projects.create(...)
            # Auto-commits on exit, rolls back on exception
    """
    
    @abstractmethod
    def __enter__(self) -> "UnitOfWork":
        """Enter the unit of work context."""
        ...
    
    @abstractmethod
    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Exit the unit of work context, commit or rollback."""
        ...
    
    @abstractmethod
    def commit(self) -> None:
        """Commit the current transaction."""
        ...
    
    @abstractmethod
    def rollback(self) -> None:
        """Rollback the current transaction."""
        ...
    
    @property
    @abstractmethod
    def session(self) -> Session:
        """Get the current database session."""
        ...


class SQLiteUnitOfWork(UnitOfWork):
    """Unit of Work implementation for SQLite databases.
    
    Features:
    - Connection pooling with WAL mode for better concurrency
    - Automatic session management
    - Transaction boundaries with commit/rollback
    - Thread-safe with check_same_thread=False
    """
    
    def __init__(
        self,
        db_path: str,
        echo: bool = False,
        pool_pre_ping: bool = True,
    ):
        """Initialize SQLite Unit of Work.
        
        Args:
            db_path: Path to SQLite database file.
            echo: If True, SQL statements will be logged.
            pool_pre_ping: Enable connection health checks.
        """
        self._db_path = db_path
        self._engine: Engine | None = None
        self._session: Session | None = None
        self._echo = echo
        self._pool_pre_ping = pool_pre_ping
    
    def _create_engine(self) -> Engine:
        """Create and configure the SQLite engine."""
        engine = create_engine(
            f"sqlite:///{self._db_path}",
            echo=self._echo,
            connect_args={"check_same_thread": False},
            pool_pre_ping=self._pool_pre_ping,
        )
        
        # Configure SQLite PRAGMAs for better concurrency
        @event.listens_for(engine, "connect")
        def set_sqlite_pragma(dbapi_conn, connection_record):
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=30000")  # 30 seconds
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()
        
        return engine
    
    @property
    def engine(self) -> Engine:
        """Get or create the database engine (lazy initialization)."""
        if self._engine is None:
            self._engine = self._create_engine()
        return self._engine
    
    @property
    def session(self) -> Session:
        """Get the current session."""
        if self._session is None:
            raise RuntimeError("No active session. Use 'with' statement.")
        return self._session
    
    def __enter__(self) -> "SQLiteUnitOfWork":
        """Start a new unit of work with a fresh session."""
        self._session = Session(self.engine)
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Commit or rollback based on exception status."""
        if self._session is None:
            return
        
        try:
            if exc_type is not None:
                self.rollback()
            else:
                self.commit()
        finally:
            self._session.close()
            self._session = None
    
    def commit(self) -> None:
        """Commit the current transaction."""
        if self._session:
            self._session.commit()
    
    def rollback(self) -> None:
        """Rollback the current transaction."""
        if self._session:
            self._session.rollback()
    
    def create_tables(self) -> None:
        """Create all tables if they don't exist."""
        SQLModel.metadata.create_all(self.engine)
    
    @contextmanager
    def transaction(self) -> Generator[Session, None, None]:
        """Context manager for a transaction within the unit of work.
        
        Use this for nested transactions or when you need explicit control.
        """
        session = Session(self.engine)
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


class PostgreSQLUnitOfWork(UnitOfWork):
    """Unit of Work implementation for PostgreSQL databases.
    
    Features:
    - Connection pooling with psycopg2/asyncpg
    - Automatic session management
    - Transaction boundaries with commit/rollback
    - Production-ready configuration
    """
    
    def __init__(
        self,
        database_url: str,
        echo: bool = False,
        pool_size: int = 5,
        max_overflow: int = 10,
        pool_pre_ping: bool = True,
    ):
        """Initialize PostgreSQL Unit of Work.
        
        Args:
            database_url: PostgreSQL connection URL.
                Format: postgresql://user:password@host:port/database
            echo: If True, SQL statements will be logged.
            pool_size: Number of connections to keep in the pool.
            max_overflow: Maximum overflow connections.
            pool_pre_ping: Enable connection health checks.
        """
        self._database_url = database_url
        self._engine: Engine | None = None
        self._session: Session | None = None
        self._echo = echo
        self._pool_size = pool_size
        self._max_overflow = max_overflow
        self._pool_pre_ping = pool_pre_ping
    
    def _create_engine(self) -> Engine:
        """Create and configure the PostgreSQL engine."""
        return create_engine(
            self._database_url,
            echo=self._echo,
            pool_size=self._pool_size,
            max_overflow=self._max_overflow,
            pool_pre_ping=self._pool_pre_ping,
        )
    
    @property
    def engine(self) -> Engine:
        """Get or create the database engine (lazy initialization)."""
        if self._engine is None:
            self._engine = self._create_engine()
        return self._engine
    
    @property
    def session(self) -> Session:
        """Get the current session."""
        if self._session is None:
            raise RuntimeError("No active session. Use 'with' statement.")
        return self._session
    
    def __enter__(self) -> "PostgreSQLUnitOfWork":
        """Start a new unit of work with a fresh session."""
        self._session = Session(self.engine)
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Commit or rollback based on exception status."""
        if self._session is None:
            return
        
        try:
            if exc_type is not None:
                self.rollback()
            else:
                self.commit()
        finally:
            self._session.close()
            self._session = None
    
    def commit(self) -> None:
        """Commit the current transaction."""
        if self._session:
            self._session.commit()
    
    def rollback(self) -> None:
        """Rollback the current transaction."""
        if self._session:
            self._session.rollback()
    
    def create_tables(self) -> None:
        """Create all tables if they don't exist."""
        SQLModel.metadata.create_all(self.engine)
    
    @contextmanager
    def transaction(self) -> Generator[Session, None, None]:
        """Context manager for a transaction within the unit of work."""
        session = Session(self.engine)
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
