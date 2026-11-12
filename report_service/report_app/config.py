from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    db_host: str = "localhost"
    db_port: int = 5433
    db_user: str = "shop"
    db_password: str = "shop"
    report_database_url: str = ""
    internal_key: str = "change-me-internal-key"

    @property
    def database_url(self) -> str:
        return self.report_database_url or (
            f"postgresql+asyncpg://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/report_db"
        )


settings = Settings()
