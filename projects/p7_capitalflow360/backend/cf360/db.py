from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from cf360.config import settings


class Base(DeclarativeBase):
    pass


engine = create_engine(settings.database_url, pool_pre_ping=True, pool_size=10, max_overflow=20)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


def get_session() -> Iterator[Session]:
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()
