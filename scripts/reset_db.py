"""Drops and recreates all service databases (a clean start with the new electronics demo data).

    python scripts/reset_db.py

Stop the services first. Needs the Postgres container to be running. ALL shop data is deleted.
"""
import asyncio
import sys
from pathlib import Path

import asyncpg

ROOT = Path(__file__).resolve().parents[1]


def env() -> dict:
    out = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            out[k.strip()] = v.split("#")[0].strip()
    return out


async def main() -> None:
    cfg = env()
    if input("This DELETES all shop data (products, orders, users, chat). Type YES to continue: ") != "YES":
        print("Cancelled.")
        return
    conn = await asyncpg.connect(host=cfg.get("DB_HOST", "localhost"), port=int(cfg.get("DB_PORT", 5433)),
                                 user=cfg.get("DB_USER", "shop"), password=cfg.get("DB_PASSWORD", "shop"),
                                 database="postgres")
    for db in ("auth_db", "ops_db", "report_db", "media_db", "chat_db", "notify_db"):
        await conn.execute(f'DROP DATABASE IF EXISTS "{db}" WITH (FORCE)')
        await conn.execute(f'CREATE DATABASE "{db}"')
        print("recreated", db)
    await conn.close()
    print("Done. Uploaded images stay in media_service/storage (delete that folder's files if you want them gone too).")


if __name__ == "__main__":
    asyncio.run(main())
