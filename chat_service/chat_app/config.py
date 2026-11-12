from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    db_host: str = "localhost"
    db_port: int = 5433
    db_user: str = "shop"
    db_password: str = "shop"
    chat_database_url: str = ""
    internal_key: str = "change-me-internal-key"
    notify_url: str = "http://localhost:8006"
    shop_name: str = "SkyShop"

    @property
    def db_name(self) -> str:
        return "chat_db"

    @property
    def database_url(self) -> str:
        return self.chat_database_url or (
            f"postgresql+asyncpg://{self.db_user}:{self.db_password}@{self.db_host}:{self.db_port}/{self.db_name}")


settings = Settings()
