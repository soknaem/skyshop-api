import asyncpg
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from .config import settings

engine = create_async_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_session():
    async with SessionLocal() as session:
        yield session


async def ensure_database() -> None:
    """Creates media_db if the Postgres volume was made before this service existed."""
    if not settings.database_url.startswith("postgresql"):
        return
    conn = await asyncpg.connect(host=settings.db_host, port=settings.db_port, user=settings.db_user,
                                 password=settings.db_password, database="postgres")
    try:
        if not await conn.fetchval("SELECT 1 FROM pg_database WHERE datname=$1", settings.db_name):
            await conn.execute(f'CREATE DATABASE "{settings.db_name}"')
    finally:
        await conn.close()
