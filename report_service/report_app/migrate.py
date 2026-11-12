"""Tiny idempotent migration so an existing v1 database keeps working (Postgres only)."""
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

STATEMENTS = [
    "ALTER TABLE order_snapshot ADD COLUMN IF NOT EXISTS user_id INTEGER",
    "ALTER TABLE order_snapshot ADD COLUMN IF NOT EXISTS fulfillment VARCHAR(10) DEFAULT 'DELIVERY'",
    "ALTER TABLE order_snapshot ADD COLUMN IF NOT EXISTS fulfillment_status VARCHAR(20) DEFAULT 'PROCESSING'",
    "ALTER TABLE order_snapshot ADD COLUMN IF NOT EXISTS subtotal NUMERIC(12,2)",
    "ALTER TABLE order_snapshot ADD COLUMN IF NOT EXISTS delivery_fee NUMERIC(12,2)",
    "ALTER TABLE order_snapshot ALTER COLUMN customer_name DROP NOT NULL",
    "ALTER TABLE order_snapshot ALTER COLUMN email DROP NOT NULL",
    "ALTER TABLE order_snapshot ALTER COLUMN phone DROP NOT NULL",
    "ALTER TABLE order_snapshot ALTER COLUMN shipping_address DROP NOT NULL",
    "ALTER TABLE order_item_snapshot ADD COLUMN IF NOT EXISTS image_url VARCHAR(500)",
]


async def run(engine: AsyncEngine) -> None:
    if engine.dialect.name != "postgresql":
        return
    async with engine.begin() as conn:
        for stmt in STATEMENTS:
            await conn.execute(text(stmt))
