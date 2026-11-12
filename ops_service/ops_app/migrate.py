"""Tiny idempotent migration so an existing v1 database keeps working (Postgres only)."""
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

STATEMENTS = [
    "ALTER TABLE product ADD COLUMN IF NOT EXISTS compare_price NUMERIC(12,2)",
    "ALTER TABLE product ADD COLUMN IF NOT EXISTS brand VARCHAR(80) DEFAULT ''",
    "ALTER TABLE product ADD COLUMN IF NOT EXISTS category_id INTEGER",
    "ALTER TABLE product ADD COLUMN IF NOT EXISTS warranty VARCHAR(60) DEFAULT ''",
    "ALTER TABLE product ADD COLUMN IF NOT EXISTS featured BOOLEAN DEFAULT FALSE",
    "ALTER TABLE product ADD COLUMN IF NOT EXISTS sold_count INTEGER DEFAULT 0",
    "ALTER TABLE product ADD COLUMN IF NOT EXISTS specs JSON",
    "ALTER TABLE product ADD COLUMN IF NOT EXISTS images JSON",
    "ALTER TABLE cart ADD COLUMN IF NOT EXISTS user_id INTEGER",
    "ALTER TABLE cart ALTER COLUMN token DROP NOT NULL",
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_cart_user ON cart(user_id)",
    "ALTER TABLE orders ADD COLUMN IF NOT EXISTS user_id INTEGER",
    "ALTER TABLE orders ADD COLUMN IF NOT EXISTS fulfillment VARCHAR(10) DEFAULT 'DELIVERY'",
    "ALTER TABLE orders ADD COLUMN IF NOT EXISTS fulfillment_status VARCHAR(20) DEFAULT 'PENDING'",
    "ALTER TABLE orders ADD COLUMN IF NOT EXISTS subtotal NUMERIC(12,2)",
    "ALTER TABLE orders ADD COLUMN IF NOT EXISTS delivery_fee NUMERIC(12,2)",
    "ALTER TABLE orders ALTER COLUMN customer_name DROP NOT NULL",
    "ALTER TABLE orders ALTER COLUMN email DROP NOT NULL",
    "ALTER TABLE orders ALTER COLUMN phone DROP NOT NULL",
    "ALTER TABLE orders ALTER COLUMN shipping_address DROP NOT NULL",
    "UPDATE orders SET subtotal = total_amount WHERE subtotal IS NULL",
    "UPDATE orders SET fulfillment_status = 'PROCESSING' WHERE status = 'PAID' AND fulfillment_status = 'PENDING'",
    "ALTER TABLE order_item ADD COLUMN IF NOT EXISTS image_url VARCHAR(500)",
]


async def run(engine: AsyncEngine) -> None:
    if engine.dialect.name != "postgresql":
        return
    async with engine.begin() as conn:
        for stmt in STATEMENTS:
            await conn.execute(text(stmt))
