from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]
SERVICE_DIR = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    db_host: str = "localhost"
    db_port: int = 5433
    db_user: str = "shop"
    db_password: str = "shop"
    media_database_url: str = ""
    internal_key: str = "change-me-internal-key"
    max_upload_mb: int = 5
    max_dimension: int = 1600
    thumb_dimension: int = 480

    @property
    def db_name(self) -> str:
        return "media_db"

    @property
    def database_url(self) -> str:
        return self.media_database_url or (
            f"postgresql+asyncpg://{self.db_user}:{self.db_password}@{self.db_host}:{self.db_port}/{self.db_name}")

    @property
    def storage_dir(self) -> Path:
        d = SERVICE_DIR / "storage"
        d.mkdir(exist_ok=True)
        return d


settings = Settings()
