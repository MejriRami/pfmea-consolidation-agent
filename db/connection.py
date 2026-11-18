"""
Connection/session factory. The connection string is NEVER hardcoded here
or anywhere in this package — it always comes from the DATABASE_URL
environment variable, exactly like OPENAI_API_KEY elsewhere in this repo.
This server hosts many unrelated production databases; nothing in this
package should be able to run without the caller explicitly supplying
which database to point at.
"""
from __future__ import annotations

import os

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker


class DatabaseNotConfigured(RuntimeError):
    pass


def get_database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise DatabaseNotConfigured(
            "DATABASE_URL is not set. Point it at the pfmea_agent database, e.g.:\n"
            '  $env:DATABASE_URL = "postgresql://user:pass@host:5432/pfmea_agent"   (PowerShell)'
        )
    return url


def get_engine(*, url: str | None = None, **kwargs) -> Engine:
    return create_engine(url or get_database_url(), **kwargs)


def get_session_factory(*, url: str | None = None) -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(url=url))
