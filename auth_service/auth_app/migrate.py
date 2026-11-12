"""Tiny idempotent migration so an existing v1 database keeps working (Postgres only)."""
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

STATEMENTS = [
    "ALTER TABLE auth_user ADD COLUMN IF NOT EXISTS name VARCHAR(120) DEFAULT ''",
    "ALTER TABLE auth_user ADD COLUMN IF NOT EXISTS phone VARCHAR(30) DEFAULT ''",
    "ALTER TABLE auth_user ADD COLUMN IF NOT EXISTS avatar_url VARCHAR(500) DEFAULT ''",
    "ALTER TABLE auth_user ADD COLUMN IF NOT EXISTS provider VARCHAR(20) DEFAULT 'local'",
    "ALTER TABLE auth_user ADD COLUMN IF NOT EXISTS provider_id VARCHAR(80)",
    "ALTER TABLE auth_user ALTER COLUMN username TYPE VARCHAR(150)",
    "UPDATE auth_user SET name = username WHERE name IS NULL OR name = ''",
    "ALTER TABLE auth_user ADD COLUMN IF NOT EXISTS token_valid_after TIMESTAMPTZ",
    "UPDATE auth_user SET provider = 'local' WHERE provider IS NULL",
]


async def run(engine: AsyncEngine) -> None:
    if engine.dialect.name != "postgresql":
        return
    async with engine.begin() as conn:
        for stmt in STATEMENTS:
            await conn.execute(text(stmt))
