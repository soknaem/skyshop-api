from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    db_host: str = "localhost"
    db_port: int = 5433
    db_user: str = "shop"
    db_password: str = "shop"
    ops_database_url: str = ""

    internal_key: str = "change-me-internal-key"
    notify_url: str = "http://localhost:8006"
    report_url: str = "http://localhost:8003"

    shop_name: str = "SkyShop"
    shop_currency: str = "KHR"
    shop_address: str = "No. 12, Street 271, Phnom Penh"
    shop_hours: str = "Mon-Sat 8:00-19:00"
    delivery_fee: float = 0
    payment_ttl_min: int = 10

    mock_mode: bool = True
    mock_paid_after: int = 0              # >0 = auto "paid" after N seconds in mock mode
    khqr_token: str = ""
    khqr_account_id: str = ""
    khqr_merchant_name: str = "My Shop"
    khqr_merchant_city: str = "Phnom Penh"
    khqr_phone: str = ""
    khqr_store_label: str = "Online"
    khqr_terminal_label: str = "WEB-01"
    khqr_expiration_days: int = 1

    @property
    def database_url(self) -> str:
        return self.ops_database_url or (
            f"postgresql+asyncpg://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/ops_db"
        )


settings = Settings()
