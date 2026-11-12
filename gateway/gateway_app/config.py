from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    auth_url: str = "http://localhost:8001"
    ops_url: str = "http://localhost:8002"
    report_url: str = "http://localhost:8003"
    media_url: str = "http://localhost:8004"
    chat_url: str = "http://localhost:8005"
    notify_url: str = "http://localhost:8006"
    internal_key: str = "change-me-internal-key"
    cors_origins: str = "http://localhost:3000"
    rate_limit_per_min: int = 600
    login_rate_limit_per_min: int = 10
    max_upload_mb: int = 5

    @property
    def origins(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()
