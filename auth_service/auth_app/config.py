from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]          # project root (holds .env)
SERVICE_DIR = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    db_host: str = "localhost"
    db_port: int = 5433
    db_user: str = "shop"
    db_password: str = "shop"
    auth_database_url: str = ""

    internal_key: str = "change-me-internal-key"
    notify_url: str = "http://localhost:8006"
    admin_username: str = "admin"
    admin_password: str = "admin123"
    access_ttl_min: int = 15
    refresh_ttl_days: int = 7

    site_url: str = "http://localhost:3000"
    google_client_id: str = ""
    google_client_secret: str = ""
    facebook_app_id: str = ""
    facebook_app_secret: str = ""
    telegram_bot_token: str = ""
    telegram_bot_username: str = ""

    reset_ttl_min: int = 30
    dev_return_reset_link: bool = False   # DEV ONLY: put the reset link in the API answer when no SMTP is set
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_tls: bool = True
    shop_name: str = "SkyShop"

    @property
    def database_url(self) -> str:
        return self.auth_database_url or (
            f"postgresql+asyncpg://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/auth_db"
        )


settings = Settings()
