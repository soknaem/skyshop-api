from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    db_host: str = "localhost"
    db_port: int = 5433
    db_user: str = "shop"
    db_password: str = "shop"
    notify_database_url: str = ""
    internal_key: str = "change-me-internal-key"
    auth_url: str = "http://localhost:8001"
    site_url: str = "http://localhost:3000"
    shop_name: str = "SkyShop"

    telegram_bot_token: str = ""
    telegram_bot_username: str = ""
    telegram_alerts: bool = True                 # master switch for admin alerts
    telegram_alert_chat_ids: str = ""            # comma separated Telegram chat ids of the shop owner/admins

    @property
    def db_name(self) -> str:
        return "notify_db"

    @property
    def database_url(self) -> str:
        return self.notify_database_url or (
            f"postgresql+asyncpg://{self.db_user}:{self.db_password}@{self.db_host}:{self.db_port}/{self.db_name}")

    @property
    def alert_chat_ids(self) -> list[str]:
        return [c.strip() for c in self.telegram_alert_chat_ids.split(",") if c.strip()]


settings = Settings()
